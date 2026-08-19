"""Motor compartilhado do parse-fatura-sicredi (CLI + API)."""

from .pipeline import (
    CSV_COLUMNS,
    ClassifiedLine,
    DroppedLine,
    classify_sources,
    classify_statement,
    lines_to_csv,
    output_name,
    sort_lines,
)
from .profiles import BankProfile, ConfigSet, OutputSchema, ProfileError, Theme
from .recategorize import (
    CategoryChange, RecategorizeError, lines_to_csv_preserving_order,
    read_output_csv, recategorize,
)
from .rules import LineState, MatchResult, Ruleset
from .statement import Entry, Statement, parse_amount, read_statement
from .text import compact, merchant_key, merchant_of, normalize, purchase_date_of
from .travel import (
    TRAVEL_CATEGORY, TravelError, TravelRange, annotate, apply_travel,
    mark_travel, purchase_range, range_of, validate_ranges,
)

__all__ = [
    "CSV_COLUMNS", "ClassifiedLine", "DroppedLine", "Entry", "LineState",
    "MatchResult", "Ruleset", "CategoryChange", "RecategorizeError",
    "lines_to_csv_preserving_order", "read_output_csv", "recategorize", "BankProfile", "ConfigSet", "OutputSchema", "ProfileError", "Theme", "Statement", "classify_sources",
    "classify_statement", "compact", "lines_to_csv", "merchant_key",
    "merchant_of", "normalize", "output_name", "parse_amount",
    "purchase_date_of", "read_statement", "sort_lines",
    "TRAVEL_CATEGORY", "TravelError", "TravelRange", "annotate", "apply_travel",
    "mark_travel", "purchase_range", "range_of", "validate_ranges",
]
