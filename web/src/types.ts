export interface Listing {
  id: number;
  source: string;
  url: string;
  address: string;
  neighborhood: string;
  beds: number | null;
  baths: number | null;
  price: number | null;
  broker_fee: number | null;
  broker_fee_source: string;
  laundry_in_unit: boolean;
  laundry_in_building: boolean;
  laundry_label?: string;
  has_flex_raw?: string;
  has_photos_raw?: string;
  dishwasher?: boolean;
  gym?: boolean;
  rooftop?: boolean;
  building_amenities: string;
  nearest_subway: string;
  has_flex: boolean | null;
  has_photos: boolean | null;
  move_in_date: string;
  listed_date: string;
  status: string;
  contact_notes: string;
  contact_name: string;
  contact_email: string;
  contact_phone: string;
  pre_tour_score: number | null;
  post_tour_score: number | null;
  notes: string;
}

export type SortKey = keyof Listing;
export type SortDir = "asc" | "desc";

export interface Filters {
  search: string;
  sources: string[];
  beds: string;
  maxPrice: string;
  statuses: string[];
  laundry: "any" | "unit" | "building";
  brokerFee: "any" | "no-fee" | "has-fee";
}
