from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

DEFAULT_COLLEGE_PROGRAM_MAP = {
    "College of Engineering": [
        "Bachelor of Science in Chemical Engineering",
        "Bachelor of Science in Food Engineering",
        "Bachelor of Science in Ceramics Engineering",
        "Bachelor of Science in Metallurgical Engineering",
        "Bachelor of Science in Civil Engineering",
        "Bachelor of Science in Sanitary Engineering",
        "Bachelor of Science in Geodetic Engineering",
        "Bachelor of Science in Geological Engineering",
        "Bachelor of Science in Transportation Systems Engineering",
        "Bachelor of Science in Electrical Engineering",
        "Bachelor of Science in Computer Engineering",
        "Bachelor of Science in Electronics Engineering",
        "Bachelor of Science in Instrumentation and Control Engineering",
        "Bachelor of Science in Mechatronics Engineering",
        "Bachelor of Science in Aerospace Engineering",
        "Bachelor of Science in Biomedical Engineering",
        "Bachelor of Science in Industrial Engineering",
        "Bachelor of Science in Mechanical Engineering",
        "Bachelor of Science in Petroleum Engineering",
        "Bachelor of Science in Automotive Engineering",
        "Bachelor of Science in Naval Architecture and Marine Engineering",
    ],
    "College of Architecture, Fine Arts and Design": [
        "Bachelor of Fine Arts and Design Major in Visual Communication",
        "Bachelor of Science in Architecture",
        "Bachelor of Science in Interior Design",
    ],
    "College of Arts and Sciences": [
        "Bachelor of Arts in English Language Studies",
        "Bachelor of Arts in Communication",
        "Bachelor of Science in Biology",
        "Bachelor of Science in Chemistry",
        "Bachelor of Science in Criminology",
        "Bachelor of Science in Development Communication",
        "Bachelor of Science in Mathematics",
        "Bachelor of Science in Psychology",
        "Bachelor of Science in Fisheries and Aquatic Sciences",
    ],
    "College of Accountancy, Business, Economics, and International Hospitality Management": [
        "Bachelor of Science in Accountancy",
        "Bachelor of Science in Business Administration Major in Business Economics",
        "Bachelor of Science in Business Administration Major in Financial Management",
        "Bachelor of Science in Business Administration Major in Human Resource Management",
        "Bachelor of Science in Business Administration Major in Marketing Management",
        "Bachelor of Science in Business Administration Major in Operations Management",
        "Bachelor of Science in Hospitality Management",
        "Bachelor of Science in Tourism Management",
        "Bachelor in Public Administration",
        "Bachelor of Science in Customs Administration",
        "Bachelor of Science in Entrepreneurship",
    ],
    "College of Informatics and Computing Sciences": [
        "Bachelor of Science in Computer Science",
        "Bachelor of Science in Information Technology",
    ],
    "College of Nursing and Allied Health Sciences": [
        "Bachelor of Science in Nursing",
        "Bachelor of Science in Nutrition and Dietetics",
        "Bachelor of Science in Public Health (Disaster Response)",
    ],
    "College of Engineering Technology": [
        "Bachelor of Automotive Engineering Technology",
        "Bachelor of Civil Engineering Technology",
        "Bachelor of Computer Engineering Technology",
        "Bachelor of Drafting Engineering Technology",
        "Bachelor of Electrical Engineering Technology",
        "Bachelor of Electronics Engineering Technology",
        "Bachelor of Food Engineering Technology",
        "Bachelor of Instrumentation and Control Engineering Technology",
        "Bachelor of Mechanical Engineering Technology",
        "Bachelor of Mechatronics Engineering Technology",
        "Bachelor of Welding and Fabrication Engineering Technology",
    ],
    "College of Agriculture and Forestry": [
        "Bachelor of Science in Agriculture",
        "Bachelor of Science in Forestry",
    ],
    "College of Teacher Education": [
        "Bachelor of Elementary Education",
        "Bachelor of Early Childhood Education",
        "Bachelor of Secondary Education Major in Science",
        "Bachelor of Secondary Education Major in English",
        "Bachelor of Secondary Education Major in Filipino",
        "Bachelor of Secondary Education Major in Mathematics",
        "Bachelor of Secondary Education Major in Social Studies",
        "Bachelor of Technology & Livelihood Education Major in Home Economics",
        "Bachelor of Technical-Vocational Teacher Education Major in Garments, Fashion and Design",
        "Bachelor of Technical-Vocational Teacher Education Major in Electronics Technology",
        "Bachelor of Physical Education",
    ],
}

OTHER_COLLEGE_LABEL = "Other / Unassigned"

PROGRAM_CODE_MAP = {
    "Bachelor of Science in Chemical Engineering": "BSChE",
    "Bachelor of Science in Food Engineering": "BSFE",
    "Bachelor of Science in Ceramics Engineering": "BSCerE",
    "Bachelor of Science in Metallurgical Engineering": "BSMetE",
    "Bachelor of Science in Civil Engineering": "BSCE",
    "Bachelor of Science in Sanitary Engineering": "BSSE",
    "Bachelor of Science in Geodetic Engineering": "BSGE",
    "Bachelor of Science in Geological Engineering": "BSGeoE",
    "Bachelor of Science in Transportation Systems Engineering": "BSTE",
    "Bachelor of Science in Electrical Engineering": "BSEE",
    "Bachelor of Science in Computer Engineering": "BSCpE",
    "Bachelor of Science in Electronics Engineering": "BSECE",
    "Bachelor of Science in Instrumentation and Control Engineering": "BSICE",
    "Bachelor of Science in Mechatronics Engineering": "BSMexE",
    "Bachelor of Science in Aerospace Engineering": "BSAeE",
    "Bachelor of Science in Biomedical Engineering": "BSBioE",
    "Bachelor of Science in Industrial Engineering": "BSIE",
    "Bachelor of Science in Mechanical Engineering": "BSME",
    "Bachelor of Science in Petroleum Engineering": "BSPetE",
    "Bachelor of Science in Automotive Engineering": "BSAE",
    "Bachelor of Science in Naval Architecture and Marine Engineering": "BSNAME",
    "Bachelor of Fine Arts and Design Major in Visual Communication": "BFAD-VC",
    "Bachelor of Science in Architecture": "BSArch",
    "Bachelor of Science in Interior Design": "BSID",
    "Bachelor of Arts in English Language Studies": "BAELS",
    "Bachelor of Arts in Communication": "BAComm",
    "Bachelor of Science in Biology": "BSBio",
    "Bachelor of Science in Chemistry": "BSChem",
    "Bachelor of Science in Criminology": "BSCrim",
    "Bachelor of Science in Development Communication": "BSDevCom",
    "Bachelor of Science in Mathematics": "BSMath",
    "Bachelor of Science in Psychology": "BSPsy",
    "Bachelor of Science in Fisheries and Aquatic Sciences": "BSFAS",
    "Bachelor of Science in Accountancy": "BSA",
    "Bachelor of Science in Business Administration Major in Business Economics": "BSBA-BE",
    "Bachelor of Science in Business Administration Major in Financial Management": "BSBA-FM",
    "Bachelor of Science in Business Administration Major in Human Resource Management": "BSBA-HRM",
    "Bachelor of Science in Business Administration Major in Marketing Management": "BSBA-MM",
    "Bachelor of Science in Business Administration Major in Operations Management": "BSBA-OM",
    "Bachelor of Science in Hospitality Management": "BSHM",
    "Bachelor of Science in Tourism Management": "BSTM",
    "Bachelor in Public Administration": "BPA",
    "Bachelor of Science in Customs Administration": "BSCA",
    "Bachelor of Science in Entrepreneurship": "BSEntrep",
    "Bachelor of Science in Computer Science": "BSCS",
    "Bachelor of Science in Information Technology": "BSIT",
    "Bachelor of Science in Nursing": "BSN",
    "Bachelor of Science in Nutrition and Dietetics": "BSND",
    "Bachelor of Science in Public Health (Disaster Response)": "BSPH-DR",
    "Bachelor of Automotive Engineering Technology": "BAET",
    "Bachelor of Civil Engineering Technology": "BCivET",
    "Bachelor of Computer Engineering Technology": "BCompET",
    "Bachelor of Drafting Engineering Technology": "BDraftET",
    "Bachelor of Electrical Engineering Technology": "BElecET",
    "Bachelor of Electronics Engineering Technology": "BElnET",
    "Bachelor of Food Engineering Technology": "BFoodET",
    "Bachelor of Instrumentation and Control Engineering Technology": "BICET",
    "Bachelor of Mechanical Engineering Technology": "BMechET",
    "Bachelor of Mechatronics Engineering Technology": "BMechatroET",
    "Bachelor of Welding and Fabrication Engineering Technology": "BWFET",
    "Bachelor of Science in Agriculture": "BSAgri",
    "Bachelor of Science in Forestry": "BSF",
    "Bachelor of Elementary Education": "BEEd",
    "Bachelor of Early Childhood Education": "BECEd",
    "Bachelor of Secondary Education Major in Science": "BSEd-SCI",
    "Bachelor of Secondary Education Major in English": "BSEd-ENG",
    "Bachelor of Secondary Education Major in Filipino": "BSEd-FIL",
    "Bachelor of Secondary Education Major in Mathematics": "BSEd-MATH",
    "Bachelor of Secondary Education Major in Social Studies": "BSEd-SST",
    "Bachelor of Technology & Livelihood Education Major in Home Economics": "BTLEd-HE",
    "Bachelor of Technical-Vocational Teacher Education Major in Garments, Fashion and Design": "BTVTEd-GFD",
    "Bachelor of Technical-Vocational Teacher Education Major in Electronics Technology": "BTVTEd-ET",
    "Bachelor of Physical Education": "BPEd",
}

PROGRAM_ALIAS_OVERRIDES = {
    "Bachelor of Science in Accountancy": {"BSA"},
    "Bachelor of Science in Architecture": {"BSARCH"},
    "Bachelor of Arts in Communication": {"BACOMM"},
    "Bachelor of Science in Biology": {"BSBIO"},
    "Bachelor of Science in Chemistry": {"BSCHEM"},
    "Bachelor of Science in Civil Engineering": {"BSCE"},
    "Bachelor of Science in Criminology": {"BSCRIM"},
    "Bachelor of Science in Computer Science": {"BSCS"},
    "Bachelor of Science in Development Communication": {"BSDEVCOM"},
    "Bachelor of Science in Electronics Engineering": {"BSECE"},
    "Bachelor of Science in Information Technology": {"BSIT"},
    "Bachelor of Science in Mathematics": {"BSMATH"},
    "Bachelor of Science in Psychology": {"BSPSY", "BSPSYCH"},
}

_PROGRAM_ACRONYM_STOPWORDS = {"of", "in", "and", "the"}
_PROGRAM_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9/&(). -]{1,20}$")


def iter_program_catalog():
    for department_name, programs in DEFAULT_COLLEGE_PROGRAM_MAP.items():
        for program_name in programs:
            yield department_name, program_name


def iter_program_catalog_records():
    for department_name, program_name in iter_program_catalog():
        yield department_name, program_name, program_code_for(program_name)


def normalize_program_name(program_name: str | None) -> str:
    return " ".join((program_name or "").split())


def program_lookup_key(program_name: str | None) -> str:
    normalized = normalize_program_name(program_name)
    return re.sub(r"[^a-z0-9]+", "", normalized.lower())


def is_program_code(program_name: str | None) -> bool:
    normalized = normalize_program_name(program_name)
    if not normalized:
        return False
    if normalized.lower().startswith("bachelor "):
        return False

    candidate = normalized.upper()
    return bool(_PROGRAM_CODE_PATTERN.fullmatch(candidate))


def _program_tokens(program_name: str | None) -> list[str]:
    normalized = normalize_program_name(program_name).replace("&", " and ")
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", normalized)
    return [token for token in cleaned.split() if token]


def _program_acronym(program_name: str | None) -> str:
    tokens = [
        token for token in _program_tokens(program_name)
        if token.lower() not in _PROGRAM_ACRONYM_STOPWORDS
    ]
    return "".join(token[0].upper() for token in tokens)


def program_code_for(program_name: str | None) -> str:
    canonical = normalize_program_name(program_name)
    if not canonical:
        return ""

    explicit_code = normalize_program_name(PROGRAM_CODE_MAP.get(canonical))
    if explicit_code:
        return explicit_code

    if " Major in " in canonical:
        base_name, major_name = canonical.split(" Major in ", 1)
        base_code = _program_acronym(base_name)
        major_code = _program_acronym(major_name)
        if base_code and major_code:
            return f"{base_code}-{major_code}"

    return _program_acronym(canonical)


def program_aliases(program_name: str | None, program_code: str | None = None) -> set[str]:
    canonical = normalize_program_name(program_name)
    if not canonical:
        return set()

    aliases = {
        canonical,
        canonical.upper(),
    }

    acronym = _program_acronym(canonical)
    if acronym:
        aliases.add(acronym)

    normalized_code = normalize_program_name(program_code) or program_code_for(canonical)
    if normalized_code:
        aliases.add(normalized_code)
        aliases.add(normalized_code.upper())

    if " Major in " in canonical:
        base_name, major_name = canonical.split(" Major in ", 1)
        base_code = _program_acronym(base_name)
        major_code = _program_acronym(major_name)
        if base_code:
            aliases.add(base_code)
        if base_code and major_code:
            aliases.add(f"{base_code}{major_code}")
            aliases.add(f"{base_code}-{major_code}")

    aliases.update(PROGRAM_ALIAS_OVERRIDES.get(canonical, set()))
    return {alias for alias in aliases if normalize_program_name(alias)}


def build_program_lookup(program_names: Iterable[str | tuple[str, str | None]]) -> dict[str, tuple[str, ...]]:
    alias_map: dict[str, set[str]] = defaultdict(set)
    canonical_programs: dict[str, str] = {}
    for entry in program_names:
        if isinstance(entry, tuple):
            program_name, program_code = entry
        else:
            program_name, program_code = entry, None

        canonical = normalize_program_name(program_name)
        if not canonical:
            continue

        normalized_code = normalize_program_name(program_code) or program_code_for(canonical)
        existing_code = canonical_programs.get(canonical, "")
        canonical_programs[canonical] = existing_code or normalized_code

    explicit_alias_keys: set[str] = set()
    explicit_alias_claims: dict[str, set[str]] = defaultdict(set)
    for canonical, program_code in canonical_programs.items():
        for alias in PROGRAM_ALIAS_OVERRIDES.get(canonical, set()):
            key = program_lookup_key(alias)
            if key:
                explicit_alias_claims[key].add(canonical)
        if program_code:
            key = program_lookup_key(program_code)
            if key:
                explicit_alias_claims[key].add(canonical)

    for key, values in explicit_alias_claims.items():
        alias_map[key].update(values)
        if len(values) == 1:
            explicit_alias_keys.add(key)

    for canonical, program_code in canonical_programs.items():
        for alias in program_aliases(canonical, program_code):
            key = program_lookup_key(alias)
            if not key or key in explicit_alias_keys:
                continue
            alias_map[key].add(canonical)

    return {
        key: tuple(sorted(values))
        for key, values in alias_map.items()
    }


def resolve_program_name(
    raw_program: str | None,
    lookup: dict[str, tuple[str, ...]] | None = None,
    *,
    registered_program: str | None = None,
) -> tuple[str, str, tuple[str, ...]]:
    normalized_raw = normalize_program_name(raw_program)
    normalized_registered = normalize_program_name(registered_program)
    raw_key = program_lookup_key(normalized_raw)
    registered_key = program_lookup_key(normalized_registered)
    lookup = lookup or {}

    if normalized_registered:
        if not normalized_raw:
            return normalized_registered, "registered", ()
        if raw_key and raw_key == registered_key:
            return normalized_registered, "registered", ()
        if is_program_code(normalized_raw):
            return normalized_registered, "registered", ()

    matches = lookup.get(raw_key, ()) if raw_key else ()
    if len(matches) == 1:
        return matches[0], "catalog", matches
    if len(matches) > 1:
        return normalized_raw, "ambiguous", matches

    if normalized_raw:
        return normalized_raw, "unmatched", ()
    if normalized_registered:
        return normalized_registered, "registered", ()
    return "", "blank", ()
