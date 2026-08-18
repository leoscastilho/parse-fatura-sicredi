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
from .rules import LineState, MatchResult, Ruleset
from .statement import Entry, Statement, parse_amount, read_statement
from .text import compact, merchant_key, merchant_of, normalize, purchase_date_of

__all__ = [
    "CSV_COLUMNS", "ClassifiedLine", "DroppedLine", "Entry", "LineState",
    "MatchResult", "Ruleset", "BankProfile", "ConfigSet", "OutputSchema", "ProfileError", "Theme", "Statement", "classify_sources",
    "classify_statement", "compact", "lines_to_csv", "merchant_key",
    "merchant_of", "normalize", "output_name", "parse_amount",
    "purchase_date_of", "read_statement", "sort_lines",
]
