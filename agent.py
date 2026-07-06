#!/usr/bin/env python3
"""NYC Apartment Search Agent — CLI entry point."""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="google")
warnings.filterwarnings("ignore", message=".*OpenSSL.*", category=Warning)
warnings.filterwarnings("ignore", message=".*LibreSSL.*", category=Warning)

import sys
from datetime import datetime
from typing import Optional

import click

from config import Config, load_preferences, load_templates, save_templates
from models.db import Contact, Listing, MessageTemplate, Tour, get_session, init_db
from services.ranker import score_all, score_listing


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_ids(ids_str: str) -> list[int]:
    return [int(i.strip()) for i in ids_str.split(",") if i.strip().isdigit()]


def _fill_template(body: str, listing: Listing, extra: dict | None = None) -> str:
    prefs = load_preferences()
    replacements = {
        "address": listing.address or "",
        "contact_name": listing.contact_name or "there",
        "beds": str(listing.beds or ""),
        "baths": str(listing.baths or ""),
        "price": str(listing.price or ""),
        "neighborhood": listing.neighborhood or "",
        "user_name": Config.USER_NAME or Config.USER_EMAIL or "Apartment Seeker",
        "tour_date": "",
        "tour_time": "",
    }
    if extra:
        replacements.update(extra)
    for k, v in replacements.items():
        body = body.replace(f"{{{k}}}", v)
    return body


def _send_outreach(listing: Listing, template: dict, channel: str, session, extra: dict | None = None) -> bool:
    body = _fill_template(template.get("body", ""), listing, extra)

    if channel == "email":
        if not listing.contact_email:
            click.echo(f"  [skip] No email for listing {listing.id} ({listing.address})")
            return False
        subject_raw = template.get("subject", "Inquiry")
        subject = _fill_template(subject_raw, listing, extra)
        from services.gmail import send_email
        msg_id = send_email(listing.contact_email, subject, body)
        click.echo(f"  [email] Sent to {listing.contact_email} — message ID {msg_id}")
    elif channel == "sms":
        to = listing.contact_phone or Config.USER_PHONE
        if not to:
            click.echo(f"  [skip] No phone for listing {listing.id}")
            return False
        from services.sms import send_sms
        sid = send_sms(to, body)
        click.echo(f"  [sms] Sent to {to} — SID {sid}")
    else:
        click.echo(f"  [skip] Unknown channel: {channel}")
        return False

    # Record in DB
    contact_record = Contact(
        listing_id=listing.id,
        channel=channel,
        message_template=template.get("key"),
        sent_at=datetime.utcnow().isoformat(),
        status="sent",
    )
    session.add(contact_record)
    listing.status = "contacted"
    listing.updated_at = datetime.utcnow().isoformat()

    # Write human-readable contact note
    date_str = datetime.utcnow().strftime("%b %-d")
    if extra and extra.get("tour_date") and extra.get("tour_time"):
        # Parse tour_date into display format
        try:
            td = datetime.strptime(extra["tour_date"], "%Y-%m-%d").strftime("%b %-d")
        except ValueError:
            td = extra["tour_date"]
        listing.contact_notes = f"tour: {td} @ {extra['tour_time']}"
    else:
        listing.contact_notes = f"inquiry: {date_str}"
    return True


# ── CLI ───────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """NYC Apartment Search Agent."""
    init_db(Config.DB_PATH)


# ── scrape ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--source", default=None, help="Scrape one source only: apartments_com | hotpads | trulia | zumper")
def scrape(source: Optional[str]):
    """Scrape listings and sync to DB + Sheet."""
    prefs = load_preferences()

    scrapers_map = {
        "renthop": "scrapers.renthop.RentHopScraper",
        "streeteasy": "scrapers.streeteasy.StreetEasyScraper",
    }

    sources = [source] if source else list(scrapers_map.keys())
    for src in sources:
        if src not in scrapers_map:
            click.echo(f"Unknown source: {src}. Choose from {list(scrapers_map.keys())}")
            continue
        module_path, class_name = scrapers_map[src].rsplit(".", 1)
        mod = __import__(module_path, fromlist=[class_name])
        scraper_cls = getattr(mod, class_name)
        scraper = scraper_cls()
        scraper.scrape(prefs)

    click.echo("\nSyncing to Google Sheet…")
    try:
        from services.sheets import pull_from_sheet, sync_to_sheet
        pull_from_sheet()
        sync_to_sheet()
    except Exception as e:
        click.echo(f"[sheets] Sync failed: {e}")


# ── sync ──────────────────────────────────────────────────────────────────────

@cli.command()
def sync():
    """Re-sync DB → Sheet without scraping."""
    try:
        from services.sheets import pull_from_sheet, sync_to_sheet
        pull_from_sheet()
        sync_to_sheet()
    except Exception as e:
        click.echo(f"[sheets] Sync failed: {e}")


# ── contact ───────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--ids", required=True, help="Comma-separated listing IDs")
@click.option("--template", "template_key", required=True, help="Template key (e.g. initial_inquiry)")
@click.option("--channel", default="email", help="email | sms")
def contact(ids: str, template_key: str, channel: str):
    """Bulk outreach to listings."""
    id_list = _parse_ids(ids)
    templates = load_templates()
    tmpl = next((t for t in templates if t["key"] == template_key), None)
    if not tmpl:
        click.echo(f"Template '{template_key}' not found. Use `messages list` to see available templates.")
        sys.exit(1)

    session = get_session(Config.DB_PATH)
    try:
        listings = session.query(Listing).filter(Listing.id.in_(id_list)).all()
        if not listings:
            click.echo("No listings found for those IDs.")
            return

        click.echo(f"\nSend '{tmpl['label']}' to {len(listings)} apartments via {channel}?")
        for l in listings:
            click.echo(f"  [{l.id}] {l.address} — {l.contact_email or l.contact_phone or 'no contact'}")

        if not click.confirm("\nProceed?", default=False):
            click.echo("Aborted.")
            return

        sent = 0
        for listing in listings:
            if _send_outreach(listing, tmpl, channel, session):
                sent += 1

        session.commit()
        click.echo(f"\nSent {sent}/{len(listings)} messages.")

        try:
            from services.sheets import sync_to_sheet
            sync_to_sheet(session)
        except Exception as e:
            click.echo(f"[sheets] Sync failed: {e}")

    finally:
        session.close()


# ── schedule-tours ────────────────────────────────────────────────────────────

@cli.command("schedule-tours")
@click.option("--date", required=True, help="Tour date (YYYY-MM-DD)")
@click.option("--ids", required=True, help="Comma-separated listing IDs")
def schedule_tours(date: str, ids: str):
    """Cluster listings, propose a tour schedule, and send tour request messages."""
    id_list = _parse_ids(ids)
    session = get_session(Config.DB_PATH)
    try:
        listings = session.query(Listing).filter(Listing.id.in_(id_list)).all()
        if not listings:
            click.echo("No listings found.")
            return

        from services.scheduler import build_schedule, print_schedule
        slots = build_schedule(listings, date, session)
        print_schedule(slots, date)

        templates = load_templates()
        tmpl = next((t for t in templates if t["key"] == "tour_request"), None)
        if not tmpl:
            click.echo("No 'tour_request' template found. Add one with `messages add`.")
            return

        if not click.confirm("Send tour request emails to all selected apartments?", default=False):
            click.echo("Aborted.")
            return

        sent = 0
        for slot in slots:
            listing = slot["listing"]
            extra = {"tour_date": date, "tour_time": slot["time"]}
            channel = tmpl.get("channel", "email")
            if channel == "both":
                channel = "email"

            if _send_outreach(listing, tmpl, channel, session, extra):
                tour = Tour(
                    listing_id=listing.id,
                    scheduled_date=date,
                    scheduled_time=slot["time"],
                    neighborhood=slot["neighborhood"],
                    confirmed=False,
                )
                session.add(tour)
                listing.status = "touring"
                sent += 1

        session.commit()
        click.echo(f"\nSent tour requests for {sent} listings. Status set to 'awaiting_confirmation'.")

        try:
            from services.sheets import sync_to_sheet
            sync_to_sheet(session)
        except Exception as e:
            click.echo(f"[sheets] Sync failed: {e}")

    finally:
        session.close()


# ── rank ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--status", default=None, help="Filter by status (e.g. liked)")
@click.option("--post-tour", "post_tour", is_flag=True, help="Rank by post-tour score")
def rank(status: Optional[str], post_tour: bool):
    """Print ranked listings table."""
    prefs = load_preferences()
    session = get_session(Config.DB_PATH)
    try:
        q = session.query(Listing).filter(Listing.status != "passed")
        if status:
            q = q.filter(Listing.status == status)
        listings = q.all()

        # Recompute pre-tour scores
        score_all(listings, prefs)
        session.commit()

        score_attr = "post_tour_score" if post_tour else "pre_tour_score"
        ranked = sorted(listings, key=lambda l: getattr(l, score_attr) or 0, reverse=True)

        label = "Post-Tour Score" if post_tour else "Pre-Tour Score"
        click.echo(f"\n{'#':<4} {'ID':<6} {'Address':<35} {'Nbhd':<20} {'$':<8} {'Beds':<5} {label}")
        click.echo("─" * 100)
        for i, listing in enumerate(ranked, 1):
            score = getattr(listing, score_attr) or 0
            click.echo(
                f"{i:<4} {listing.id:<6} {(listing.address or '')[:33]:<35} "
                f"{(listing.neighborhood or '')[:18]:<20} "
                f"${listing.price or 0:<7} {listing.beds or '':<5} {score:.1f}"
            )

        try:
            from services.sheets import sync_to_sheet
            sync_to_sheet(session)
        except Exception as e:
            click.echo(f"[sheets] Score sync failed: {e}")

    finally:
        session.close()


# ── mark-replied ──────────────────────────────────────────────────────────────

@cli.command("mark-replied")
@click.option("--id", "listing_id", required=True, type=int)
@click.option("--snippet", default=None, help="Optional reply snippet to save")
def mark_replied(listing_id: int, snippet: Optional[str]):
    """Log a broker reply for a listing."""
    session = get_session(Config.DB_PATH)
    try:
        contact = (
            session.query(Contact)
            .filter_by(listing_id=listing_id, status="sent")
            .order_by(Contact.sent_at.desc())
            .first()
        )
        if not contact:
            click.echo(f"No sent contact record found for listing {listing_id}.")
            return

        contact.replied_at = datetime.utcnow().isoformat()
        contact.status = "replied"
        if snippet:
            contact.reply_snippet = snippet

        listing = session.get(Listing, listing_id)
        if listing:
            listing.updated_at = datetime.utcnow().isoformat()

        session.commit()
        click.echo(f"Marked listing {listing_id} as replied.")
        try:
            from services.sheets import sync_to_sheet
            sync_to_sheet(session)
        except Exception as e:
            click.echo(f"[sheets] Sync failed: {e}")
    finally:
        session.close()


# ── mark-confirmed ────────────────────────────────────────────────────────────

@cli.command("mark-confirmed")
@click.option("--id", "listing_id", required=True, type=int)
@click.option("--date", required=True, help="Confirmed tour date (YYYY-MM-DD)")
@click.option("--time", "tour_time", required=True, help='Confirmed tour time (e.g. "10:00 AM")')
def mark_confirmed(listing_id: int, date: str, tour_time: str):
    """Confirm a tour and create a Google Calendar event."""
    session = get_session(Config.DB_PATH)
    try:
        listing = session.get(Listing, listing_id)
        if not listing:
            click.echo(f"Listing {listing_id} not found.")
            return

        tour = (
            session.query(Tour)
            .filter_by(listing_id=listing_id, confirmed=False)
            .order_by(Tour.id.desc())
            .first()
        )
        if not tour:
            tour = Tour(listing_id=listing_id)
            session.add(tour)

        tour.scheduled_date = date
        tour.scheduled_time = tour_time
        tour.confirmed = True
        tour.neighborhood = listing.neighborhood

        # Create calendar event
        try:
            from services.calendar_service import create_tour_event
            event_id = create_tour_event(listing, date, tour_time)
            tour.calendar_event_id = event_id
            click.echo(f"✓ Calendar event created: {event_id}")
        except Exception as e:
            click.echo(f"[calendar] Failed to create event: {e}")

        listing.status = "touring"
        listing.updated_at = datetime.utcnow().isoformat()
        session.commit()

        click.echo(f"✓ Tour confirmed: {listing.address} — {date} @ {tour_time}")

        try:
            from services.sheets import sync_to_sheet
            sync_to_sheet(session)
        except Exception as e:
            click.echo(f"[sheets] Sync failed: {e}")

    finally:
        session.close()


# ── mark-status ───────────────────────────────────────────────────────────────

@cli.command("mark-status")
@click.option("--id", "listing_id", required=True, type=int)
@click.option("--status", required=True, help="new | liked | contacted | touring | passed")
def mark_status(listing_id: int, status: str):
    """Update a listing's status."""
    valid = {"new", "liked", "contacted", "touring", "passed", "awaiting_confirmation"}
    if status not in valid:
        click.echo(f"Invalid status. Choose from: {', '.join(sorted(valid))}")
        sys.exit(1)

    session = get_session(Config.DB_PATH)
    try:
        listing = session.get(Listing, listing_id)
        if not listing:
            click.echo(f"Listing {listing_id} not found.")
            return
        listing.status = status
        listing.updated_at = datetime.utcnow().isoformat()
        session.commit()
        click.echo(f"Listing {listing_id} status → {status}")

        try:
            from services.sheets import sync_to_sheet
            sync_to_sheet(session)
        except Exception as e:
            click.echo(f"[sheets] Sync failed: {e}")
    finally:
        session.close()


# ── messages ──────────────────────────────────────────────────────────────────

@cli.group()
def messages():
    """Manage saved message templates."""


@messages.command("list")
def messages_list():
    """Show all saved templates."""
    templates = load_templates()
    if not templates:
        click.echo("No templates saved.")
        return
    for t in templates:
        click.echo(f"\n[{t['key']}] {t['label']} ({t['channel']})")
        if t.get("subject"):
            click.echo(f"  Subject: {t['subject']}")
        click.echo(f"  Body: {t['body'][:80]}…")


@messages.command("add")
def messages_add():
    """Interactively create a new template."""
    key = click.prompt("Key (unique identifier, e.g. follow_up)")
    label = click.prompt("Label (human-readable name)")
    channel = click.prompt("Channel", type=click.Choice(["email", "sms", "both"]))
    subject = None
    if channel in ("email", "both"):
        subject = click.prompt("Subject")
    body = click.prompt("Body (use {address}, {contact_name}, {beds}, {baths}, {price}, {user_name}, {tour_date}, {tour_time})")

    templates = load_templates()
    if any(t["key"] == key for t in templates):
        click.echo(f"Template '{key}' already exists. Use `messages edit` to update it.")
        return

    templates.append({"key": key, "label": label, "channel": channel, "subject": subject, "body": body})
    save_templates(templates)
    click.echo(f"Template '{key}' saved.")


@messages.command("edit")
@click.option("--key", required=True)
def messages_edit(key: str):
    """Edit an existing template."""
    templates = load_templates()
    tmpl = next((t for t in templates if t["key"] == key), None)
    if not tmpl:
        click.echo(f"Template '{key}' not found.")
        return

    tmpl["label"] = click.prompt("Label", default=tmpl["label"])
    tmpl["channel"] = click.prompt("Channel", default=tmpl["channel"], type=click.Choice(["email", "sms", "both"]))
    if tmpl["channel"] in ("email", "both"):
        tmpl["subject"] = click.prompt("Subject", default=tmpl.get("subject") or "")
    tmpl["body"] = click.prompt("Body", default=tmpl["body"])

    save_templates(templates)
    click.echo(f"Template '{key}' updated.")


@messages.command("delete")
@click.option("--key", required=True)
def messages_delete(key: str):
    """Delete a template."""
    templates = load_templates()
    new_templates = [t for t in templates if t["key"] != key]
    if len(new_templates) == len(templates):
        click.echo(f"Template '{key}' not found.")
        return
    if click.confirm(f"Delete template '{key}'?", default=False):
        save_templates(new_templates)
        click.echo(f"Template '{key}' deleted.")


# ── run-daily ─────────────────────────────────────────────────────────────────

@cli.command("run-daily")
def run_daily():
    """Run scrape + sheet sync (intended for cron)."""
    click.echo(f"[{datetime.now().isoformat()}] Starting daily run…")
    ctx = cli.make_context("cli", [])
    with ctx:
        scrape_ctx = scrape.make_context("scrape", [], parent=ctx)
        with scrape_ctx:
            scrape.invoke(scrape_ctx)


if __name__ == "__main__":
    cli()
