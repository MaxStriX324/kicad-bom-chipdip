#!/usr/bin/env python3
"""Convert grouped KiCad BOM to safe ChipDip import and complete lookup checklist.

Requires Python 3.9+. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


REF_SPLIT = re.compile(r"\s*[,;]\s*")
SPACES = re.compile(r"\s+")
PART_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./-]*$")
NOMINAL_PATTERN = re.compile(
    r"^(?:\d+(?:[.,]\d+)?\s*(?:[kKmMrR]|[pnumµ]?[FfHh]|[kKmM]?[Hh][Zz]|[Vv]|[Aa])?"
    r"(?:\s+\d+(?:[.,]\d+)?\s*[Vv])?)$"
)


@dataclass
class BomItem:
    reference: str
    qty: int
    value: str
    footprint: str
    part: str
    status: str
    reason: str
    source: Dict[str, str] = field(default_factory=dict)


def normalize_header(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value).strip().casefold())


def read_csv_table(path: Path, require_value: bool = True) -> Tuple[List[Dict[str, str]], List[str]]:
    raw = path.read_bytes()
    decoded = None
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            decoded = raw.decode(encoding)
            break
        except UnicodeError:
            continue
    if decoded is None:
        raise ValueError("Не удалось определить кодировку CSV")
    # Determine CSV delimiter from the header, not data: quoted prose fields
    # may contain many semicolons or commas and confuse csv.Sniffer.
    import io
    first_line = decoded.splitlines()[0] if decoded.splitlines() else ""
    header_columns = {
        delim: len(next(csv.reader(io.StringIO(first_line), delimiter=delim), []))
        for delim in (",", ";", "\t")
    }
    delimiter = max(header_columns, key=header_columns.get)
    from io import StringIO
    reader = csv.DictReader(StringIO(decoded, newline=""), delimiter=delimiter)
    if reader.fieldnames is None:
        raise ValueError("Пустой CSV")
    columns = {normalize_header(h): h for h in reader.fieldnames if h is not None}
    if "reference" not in columns or (require_value and "value" not in columns):
        raise ValueError("Нужны колонки Reference и Value (формат KiCad BOM CSV)")
    rows = [{normalize_header(k): (v or "").strip() for k, v in row.items() if k is not None}
            for row in reader if row and any((v or "").strip() for v in row.values())]
    return rows, list(columns)


def get(row: Dict[str, str], *names: str) -> str:
    return next((row[key] for name in names if (key := normalize_header(name)) in row and row[key]), "")


def excluded(row: Dict[str, str]) -> bool:
    def is_true(value: str) -> bool:
        return value.strip().casefold() not in {"", "0", "false", "off", "no", "n", "нет", "-"}
    return any(is_true(get(row, field)) for field in ("DNP", "Exclude from BOM"))


def validate_part(value: str) -> str:
    value = SPACES.sub(" ", value.strip())
    if any(c in value for c in ("\r", "\n", "\t", ";")):
        raise ValueError(f"Недопустимый символ в названии компонента: {value!r}")
    return value


def package_code(footprint: str) -> str:
    match = re.search(r"(?:^|[_:])(?:R|C|L)_(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?:_|$)", footprint)
    return match.group(1) if match else ""


def looks_like_part_number(value: str) -> bool:
    # Site search accepts 'part number + manufacturer', e.g. '1N4148 NXP'.
    tokens = value.split()
    if len(tokens) == 2 and re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{1,30}", tokens[1]):
        return looks_like_part_number(tokens[0])
    if not PART_PATTERN.fullmatch(value):
        return False
    if not re.search(r"\d", value) or not re.search(r"[A-Za-z]", value):
        return False
    if NOMINAL_PATTERN.fullmatch(value):
        return False
    # KiCad library values may deliberately use lowercase x as an unknown suffix/variant.
    if value.endswith("x") or re.search(r"(?<=[A-Z0-9])x(?=[A-Z0-9])", value):
        return False
    return True


def classify_value(value: str, footprint: str, reference: str,
                   include_nominals: bool) -> Tuple[str, str, str]:
    value = validate_part(value)
    prefix = re.match(r"[A-Za-z]+", reference)
    family = prefix.group(0).upper() if prefix else ""

    if looks_like_part_number(value):
        return value, "candidate", "Обозначение похоже на артикул; проверьте производителя и вариант"

    if family in {"R", "C", "L", "Y"} and NOMINAL_PATTERN.fullmatch(value):
        pkg = package_code(footprint)
        if include_nominals and pkg:
            return f"{value} {pkg}", "nominal_search", "Поиск по номиналу/корпусу; НЕ точный артикул"
        return "", "unresolved", "Номинал без точного артикула производителя"

    if "x" in value and re.search(r"[A-Za-z]\d|\d[A-Za-z]", value):
        return "", "unresolved", "В обозначении может быть незаданный вариант (x)"
    return "", "unresolved", "Условное название или неполный артикул"


def load_mapping(path: Optional[Path]) -> Tuple[Dict[str, str], Dict[Tuple[str, str], str]]:
    by_ref: Dict[str, str] = {}
    by_value_footprint: Dict[Tuple[str, str], str] = {}
    if path is None:
        return by_ref, by_value_footprint
    rows, _ = read_csv_table(path, require_value=False)
    for num, row in enumerate(rows, 2):
        part = validate_part(get(row, "PartNumber", "MPN", "ChipDip", "ChipDip SKU"))
        if not part:
            continue
        ref = get(row, "Reference")
        value, footprint = get(row, "Value"), get(row, "Footprint")
        if ref:
            key = ref.casefold()
            if key in by_ref and by_ref[key] != part:
                raise ValueError(f"Конфликт артикула для {ref} в строке {num} файла сопоставления")
            by_ref[key] = part
        elif value:
            key2 = (value.casefold(), footprint.casefold())
            if key2 in by_value_footprint and by_value_footprint[key2] != part:
                raise ValueError(f"Конфликт артикула для {value} в строке {num}")
            by_value_footprint[key2] = part
    return by_ref, by_value_footprint


def convert(rows: List[Dict[str, str]], mapping_ref: Dict[str, str],
            mapping_value: Dict[Tuple[str, str], str], boards: int = 1,
            include_nominals: bool = False) -> Tuple[List[BomItem], List[str]]:
    if boards <= 0:
        raise ValueError("Число плат должно быть положительным")
    result: List[BomItem] = []
    warnings: List[str] = []
    seen_refs = set()
    for lineno, row in enumerate(rows, 2):
        ref = get(row, "Reference")
        value = get(row, "Value")
        footprint = get(row, "Footprint")
        if excluded(row):
            continue
        ref_list = [r.strip() for r in REF_SPLIT.split(ref) if r.strip()]
        raw_qty = get(row, "Qty", "Quantity")
        if raw_qty:
            try:
                qty = int(raw_qty)
            except ValueError as exc:
                raise ValueError(f"Строка {lineno}: Qty не целое число: {raw_qty!r}") from exc
        else:
            qty = len(ref_list)
            warnings.append(f"Строка {lineno}: Qty отсутствует, взято {qty} из Reference")
        if qty <= 0:
            warnings.append(f"Строка {lineno}: Qty={qty}, позиция пропущена")
            continue
        if ref_list and len(ref_list) != qty:
            warnings.append(f"Строка {lineno}: Reference содержит {len(ref_list)}, а Qty={qty}")
        for r in ref_list:
            k = r.casefold()
            if k in seen_refs:
                warnings.append(f"Строка {lineno}: повторяется Reference {r}")
            seen_refs.add(k)

        part = validate_part(get(row, "ChipDip", "ChipDip SKU", "ChipDip Part Number",
                                 "Manufacturer Part Number", "MPN", "MP", "PartNumber"))
        if part:
            status, reason = "explicit", "Артикул указан в поле BOM"
        else:
            part = mapping_ref.get(ref.casefold(), "")
            if not part:
                # Also match single references for a BOM that was regrouped by KiCad.
                mapped_refs = [mapping_ref.get(r.casefold(), "") for r in ref_list]
                if mapped_refs and all(mapped_refs) and len(set(mapped_refs)) == 1:
                    part = mapped_refs[0]
            if not part:
                part = mapping_value.get((value.casefold(), footprint.casefold()), "")
            if part:
                status, reason = "mapped", "Артикул взят из пользовательского файла сопоставления"
            else:
                part, status, reason = classify_value(value, footprint, ref, include_nominals)
        result.append(BomItem(ref, qty * boards, value, footprint, part, status, reason, dict(row)))
    return result, warnings


def write_csv(path: Path, headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(headers)
        writer.writerows(rows)


def family_of(reference: str) -> str:
    match = re.match(r"[A-Za-z]+", reference)
    return match.group(0).upper() if match else ""


def russian_nominal(value: str, family: str) -> str:
    """Convert only evident KiCad nominal units; do not infer electrical specs."""
    compact = value.strip().replace("µ", "u").replace("μ", "u")
    if family == "R":
        match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*([kKmM]?)", compact)
        if match:
            unit = {"": "Ом", "k": "кОм", "m": "МОм"}[match.group(2).lower()]
            return match.group(1).replace(".", ",") + " " + unit
    if family == "C":
        match = re.match(r"^(\d+(?:[.,]\d+)?)\s*(p|n|u|m)?F(?=\s|$)", compact, flags=re.I)
        if match:
            unit = {"p": "пФ", "n": "нФ", "u": "мкФ", "m": "мФ", None: "Ф"}[match.group(2).lower() if match.group(2) else None]
            return match.group(1).replace(".", ",") + " " + unit
    if family == "L":
        match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(n|u|m)?H", compact, flags=re.I)
        if match:
            unit = {"n": "нГн", "u": "мкГн", "m": "мГн", None: "Гн"}[match.group(2).lower() if match.group(2) else None]
            return match.group(1).replace(".", ",") + " " + unit
    return value.strip()


def footprint_hint(footprint: str) -> str:
    match = re.search(r"(?:^|[_:])(?:R|C|L)_(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?:_|$)", footprint)
    if match:
        return match.group(1)
    for pattern in (r"LQFP-\d+", r"TQFP-\d+", r"SOIC-\d+", r"SOT-\d+(?:-\d+)?", r"DIP-\d+", r"SMA(?:F)?", r"SMB", r"SMC", r"HC49-U"):
        match = re.search(pattern, footprint, flags=re.I)
        if match:
            return match.group(0).upper()
    return ""


def search_query(item: BomItem) -> str:
    """Human-readable LOOKUP phrase. It is not a supplier part number."""
    if item.part and item.status in {"explicit", "mapped"}:
        return validate_part(item.part)
    fam = family_of(item.reference)
    value = validate_part(item.value)
    fp = item.footprint
    package = footprint_hint(fp)
    if fam == "R":
        nominal = russian_nominal(value, "R")
        kind = "резистор-перемычка SMD" if nominal == "0 Ом" else "резистор SMD"
        return validate_part(f"{kind} {nominal} {package}")
    if fam == "C":
        nominal = russian_nominal(value, "C")
        voltage = re.search(r"(?:^|\s)(\d+(?:[.,]\d+)?)\s*[Vv](?:\b|$)", value)
        voltage_part = (f" {voltage.group(1)} В" if voltage else "")
        if "CP_Radial" in fp or "C_Radial" in fp:
            dimensions = re.search(r"D(\d+(?:[.,]\d+)?)mm_P(\d+(?:[.,]\d+)?)mm", fp)
            dim = (f" диаметр {dimensions.group(1)} мм шаг {dimensions.group(2)} мм" if dimensions else "")
            return validate_part(f"конденсатор электролитический {nominal}{voltage_part}{dim}")
        return validate_part(f"конденсатор керамический SMD {nominal}{voltage_part} {package}")
    if fam == "L":
        kind = "ферритовая бусина" if value.upper().startswith("BLM") else "дроссель"
        if item.part and item.status == "candidate":
            return validate_part(f"{kind} {item.part} {package}")
        return validate_part(f"{kind} {russian_nominal(value, 'L')} {package or ('ANR5045' if 'ANR5045' in fp else '')}")
    if fam == "TP":
        # May be a footprint-only test point; never claim it must be populated.
        return "контакт тестовый штыревой 1x01 шаг 2,54 мм"
    if fam == "J":
        connector = fp.split(":", 1)[-1]
        known = re.search(r"(PhoenixContact_(?:G?MSTBA)_2,5_[234]-G|JST_XH_B2B-XH-A)", connector)
        if known:
            name = known.group(1).replace("_", " ")
        elif "IDC-Header" in connector:
            name = "разъём IDC штыревой"
        else:
            name = "разъём штыревой"
        positions = re.search(r"(\d+x\d+)", connector)
        pitch = re.search(r"P(\d+(?:[.,]\d+)?)mm", connector)
        direction = "вертикальный" if "Vertical" in connector else ""
        return validate_part(" ".join(x for x in (name, positions.group(1) if positions else "", f"шаг {pitch.group(1)} мм" if pitch else "", direction) if x))
    if fam == "K":
        if "SANYOU" in value.upper() or "SANYOU" in fp.upper():
            return "реле SANYOU SRD SPDT напряжение катушки уточнить"
        return validate_part(f"реле {value}")
    if fam == "SW":
        if "RotaryEncoder" in value or "PEC11R" in fp:
            m = re.search(r"PEC11R-\d+[A-Za-z0-9-]*", fp)
            candidate = m.group(0).removesuffix("-0-0-0") if m else value
            return validate_part(f"энкодер механический {candidate}")
        return "кнопка тактовая 6x6 мм высота 7,3 мм"
    if fam in {"D", "LED"} and "LED" in value.upper():
        color = "зелёный" if "LED_G" in value.upper() else "красный" if "LED_R" in value.upper() else "RGB" if "RGB" in fp.upper() else "цвет уточнить"
        package = "0805" if "0805" in fp else "5 мм 4 вывода" if "D5.0mm-4" in fp else ""
        return validate_part(f"светодиод {color} {package}")
    if fam == "BZ":
        return "зуммер 12x9,5 мм шаг 7,6 мм напряжение уточнить"
    if fam == "Y":
        crystal = russian_nominal(value, "L")  # no inference for resonators
        return validate_part(f"кварцевый резонатор {crystal} {package}")
    if value.startswith("ACS712x"):
        return "датчик тока ACS712 30A SOIC-8 вариант уточнить"
    if value.startswith("STM32F405VGTx"):
        return "STM32F405VG LQFP-100 точный суффикс уточнить"
    if item.part and item.status == "candidate":
        # Add footprint for discrimination, e.g. PC817 DIP-4 versus modules.
        return validate_part(f"{item.part} {package}" if package else item.part)
    return validate_part(f"{value} {package}" if package else value or fp.split(':')[-1])


def review_note(item: BomItem) -> Tuple[str, str]:
    """Priority + unresolved parameter(s), without fabricating an MPN."""
    if item.status in {"explicit", "mapped"}:
        return "low", "Артикул задан, но совместимость и наличие в ChipDip не проверены"
    fam = family_of(item.reference)
    if fam == "TP":
        return "high", "Уточнить, устанавливается ли физический штырь или это только тестовая точка"
    if fam == "C":
        return "medium", "Проверить диэлектрик, допуск, рабочее напряжение и размеры (особенно высоту)"
    if fam == "R":
        return "medium", "Проверить мощность, точность, температурный коэффициент и корпус"
    if fam == "J":
        return "high", "Требуется заводской артикул и проверка механики, шага, высоты, ответной части"
    if fam == "K":
        return "high", "Проверить напряжение катушки, ток контактов, распиновку и посадочное место"
    if fam == "SW":
        return "high", "Проверить размеры, посадочное место, распиновку и исполнение"
    if fam == "BZ":
        return "high", "Проверить активный/пассивный, напряжение, ток и распиновку"
    if fam == "L":
        return "high", "Проверить ток насыщения, DCR, корпус и назначение феррит/дроссель"
    if fam == "Y":
        return "high", "Проверить нагрузочную емкость, ESR, корпус и точность"
    if "x" in item.value and re.search(r"[A-Za-z]\d|\d[A-Za-z]", item.value):
        return "high", "Уточнить неизвестный символ x в заказном обозначении"
    if fam == "D" and "LED" in item.value.upper():
        return "high", "Проверить цвет, яркость, полярность и распиновку"
    if fam in {"U", "PS", "Q"}:
        return "high", "Проверить полное заказное обозначение, корпус, функции и параметры"
    return "medium", "Нужен подтвержденный артикул производителя и сверка с платой"


def write_outputs(source: Path, items: List[BomItem], warnings: List[str],
                  dest: Path, include_nominals: bool) -> Dict[str, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    paths = {
        "chipdip": dest / f"{stem}_chipdip.txt",  # ALL KiCad components, including search descriptions
        "mpn_only": dest / f"{stem}_chipdip_mpn_only.txt",
        "candidates": dest / f"{stem}_chipdip_candidates_only.txt",
        "search": dest / f"{stem}_search_all.txt",
        "review": dest / f"{stem}_review.csv",
        "needs_review": dest / f"{stem}_needs_review.csv",
        "mapping": dest / f"{stem}_mapping_template.csv",
        "summary": dest / f"{stem}_summary.txt",
    }
    def group_queries(entries: List[Tuple[str, int]]) -> OrderedDict[str, Tuple[str, int]]:
        grouped: OrderedDict[str, Tuple[str, int]] = OrderedDict()
        for phrase, quantity in entries:
            key = SPACES.sub(" ", phrase).casefold()
            if key in grouped:
                name, total = grouped[key]
                grouped[key] = (name, total + quantity)
            else:
                grouped[key] = (phrase, quantity)
        return grouped

    # Human-readable full list and purchase-ready MPNs remain SEPARATE.
    # The ChipDip importer accepts search phrases but cannot validate them as parts.
    upload_items = [item for item in items if item.status in {"explicit", "mapped"} and item.part]
    candidate_items = [item for item in items if item.status == "candidate" and item.part]
    upload_queries = group_queries([(item.part, item.qty) for item in upload_items])
    candidate_queries = group_queries([(item.part, item.qty) for item in candidate_items])
    all_queries = group_queries([(search_query(item), item.qty) for item in items])
    for label, grouped in (("chipdip", all_queries),
                           ("mpn_only", upload_queries),
                           ("candidates", candidate_queries),
                           ("search", all_queries)):
        with paths[label].open("w", encoding="utf-8", newline="\n") as stream:
            for phrase, qty in grouped.values():
                stream.write(f"{phrase}\t{qty}\n")
    total_bom = sum(item.qty for item in items)
    total_search = sum(qty for _, qty in all_queries.values())
    if total_search != total_bom:
        raise ValueError("Ошибка подсчета: итог поискового TXT не совпадает с BOM")
    total_upload = sum(item.qty for item in upload_items)
    total_candidates = sum(item.qty for item in candidate_items)
    total_to_choose = total_bom - total_upload - total_candidates
    if total_upload + total_candidates + total_to_choose != total_bom:
        raise ValueError("Ошибка подсчета: не все компоненты отнесены к группе")

    # Preserve every exported KiCad field in the review, including user-defined
    # columns (MP, MF, MANUFACTURER, Описание, etc). Unknown columns are harmless.
    source_columns = list(dict.fromkeys(key for item in items for key in item.source))
    write_csv(paths["review"],
              ["Reference", "Qty (boards)", "Value", "Footprint", "PartNumber", "Status", "Reason", "SearchQuery", "ImportReady"]
              + [f"KiCad:{key}" for key in source_columns],
              [[i.reference, i.qty, i.value, i.footprint, i.part, i.status, i.reason, search_query(i), "yes" if i.status in {"explicit", "mapped"} and i.part else "no"]
               + [i.source.get(key, "") for key in source_columns] for i in items])
    write_csv(paths["needs_review"],
              ["Reference", "Qty", "Value", "Footprint", "SearchQuery", "Priority", "CheckBeforeOrder", "CandidateMPN", "ConfirmedPartNumber"],
              [[i.reference, i.qty, i.value, i.footprint, search_query(i),
                *review_note(i), i.part if i.status == "candidate" else "", ""]
               for i in items if i.status not in {"explicit", "mapped"}])
    if not paths["mapping"].exists():
        write_csv(paths["mapping"],
                  ["Reference", "Value", "Footprint", "Qty", "Suggested", "PartNumber"],
                  [[i.reference, i.value, i.footprint, i.qty, i.part, ""] for i in items
                   if i.status not in {"mapped", "explicit"}])
    counts = {s: sum(i.qty for i in items if i.status == s)
              for s in ("explicit", "mapped", "candidate", "nominal_search", "unresolved")}
    lines = [
        f"Источник: {source.name}",
        f"Строк BOM к установке: {len(items)}",
        f"Всего компонентов: {total_bom}",
        f"ОСНОВНОЙ TXT (*_chipdip.txt): {len(all_queries)} строк, {total_search} шт. из BOM.",
        f"Подтвержденные MPN (*_chipdip_mpn_only.txt): {len(upload_queries)} артикулов, {total_upload} шт.",
        f"Артикулы-кандидаты (*_chipdip_candidates_only.txt): {len(candidate_queries)} наименований, {total_candidates} шт.",
        f"Нужен подбор артикула: {total_to_choose} шт.",
        f"Полный поисковый список (*_search_all.txt): {len(all_queries)} строк, {total_search} шт.",
        f"Артикулы из BOM (MP, MPN, ChipDip и др.): {counts['explicit']} шт.",
        f"Артикулы из файла сопоставления: {counts['mapped']} шт.",
        f"Названия из Value (непроверенные): {counts['candidate']} шт.",
        f"Поисковые фразы по номиналу: {counts['nominal_search']} шт.",
        f"Прочие позиции без MPN: {counts['unresolved']} шт.",
        "",
        "*_chipdip.txt: ПОЛНАЯ спецификация для поисковой загрузки в ChipDip.",
        "ВАЖНО: часть строк содержит номиналы/описания, а не артикулы товара.",
        "Найденные ChipDip совпадения НЕ означают совместимость компонентов.",
        "*_chipdip_mpn_only.txt: только явно заданные или сопоставленные MPN.",
        "Даже MPN не проверены по наличию товара в каталоге ChipDip.",
        "*_chipdip_candidates_only.txt: возможные артикулы; нужно проверить",
        "производителя, конкретный вариант, корпус и характеристики.",
        "*_search_all.txt: копия полного поискового списка (совместимость с v5).",
        "Подтвердите артикулы в mapping_template.csv, затем повторите запуск с --mapping.",
    ]
    review_qty = sum(i.qty for i in items if i.status not in {"explicit", "mapped"})
    high_priority_qty = sum(i.qty for i in items if i.status not in {"explicit", "mapped"} and review_note(i)[0] == "high")
    lines += [f"Требуют технической проверки: {review_qty} шт.",
              f"Из них с высоким приоритетом: {high_priority_qty} шт.",
              "*_needs_review.csv: очередь для подбора артикулов с причинами и приоритетами."]
    if warnings:
        lines.extend(["", "Предупреждения:", *["- " + w for w in warnings]])
    paths["summary"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return paths


def pick_csv() -> Optional[Path]:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(title="Выберите CSV BOM из KiCad",
                                              filetypes=[("KiCad BOM", "*.csv"), ("Все файлы", "*.*")])
        root.destroy()
        return Path(selected) if selected else None
    except Exception:
        # Some Windows Python distributions omit Tcl/Tk. A console path is
        # still usable and does not require internet or extra dependencies.
        print("Окно выбора недоступно. Введите полный путь к CSV файлу:")
        try:
            value = input("> ").strip().strip('"')
        except EOFError:
            raise ValueError("Укажите путь к файлу: start.bat \"C:\\path\\bom.csv\"")
        return Path(value) if value else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="KiCad CSV BOM → текстовый список ChipDip")
    parser.add_argument("input", nargs="?", type=Path, help="CSV BOM; без аргумента откроется окно выбора")
    parser.add_argument("--boards", type=int, help="Количество одинаковых плат (перекрывает settings.json)")
    parser.add_argument("--mapping", type=Path, help="CSV соответствий; без аргумента ищем файл рядом с BOM")
    parser.add_argument("--no-auto-mapping", action="store_true", help="Не искать рядом с BOM *._mapping.csv")
    parser.add_argument("--out", type=Path, help="Каталог результатов (по умолчанию рядом с входным CSV)")
    parser.add_argument("--include-nominals", action="store_true", default=None,
                        help="Добавить номиналы с известным SMD-корпусом как поисковые фразы (не MPN)")
    parser.add_argument("--settings", type=Path, help="Путь к JSON настройкам (по умолчанию рядом со скриптом)")
    args = parser.parse_args(argv)
    try:
        settings_path = args.settings or Path(__file__).resolve().with_name("settings.json")
        settings = {}
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
            if not isinstance(settings, dict):
                raise ValueError("Настройки должны быть JSON-объектом")
        elif args.settings is not None:
            raise ValueError(f"Нет файла настроек: {settings_path}")
        boards = args.boards if args.boards is not None else int(settings.get("boards", 1))
        include_nominals = (args.include_nominals if args.include_nominals is not None else
                            bool(settings.get("include_nominals", False)))
        mapping = args.mapping
        if mapping is None and settings.get("mapping_file"):
            mapping = Path(settings["mapping_file"])
            if not mapping.is_absolute():
                mapping = settings_path.parent / mapping
        out = args.out
        if out is None and settings.get("output_dir"):
            out = Path(settings["output_dir"])
            if not out.is_absolute():
                out = settings_path.parent / out
        path = args.input or pick_csv()
        if path is None:
            print("Выбор отменён")
            return 0
        if not path.is_file():
            raise ValueError(f"Нет такого CSV: {path}")
        rows, _ = read_csv_table(path)
        if mapping is None and not args.no_auto_mapping and settings.get("auto_mapping", True):
            for suffix in ("_mapping.csv", "_mapping_template.csv"):
                candidate = path.with_name(path.stem + suffix)
                if candidate.is_file():
                    mapping = candidate
                    break
        if mapping is not None:
            if not mapping.is_file():
                raise ValueError(f"Файл соответствий не найден: {mapping}")
            print(f"Соответствия: {mapping}")
        else:
            print("Соответствия: не найдены; неизвестные позиции получат поисковое описание")
        mapping_ref, mapping_value = load_mapping(mapping)
        items, warnings = convert(rows, mapping_ref, mapping_value, boards,
                                  include_nominals)
        outputs = write_outputs(path, items, warnings, out or path.parent,
                                include_nominals)
        print("\n".join([f"Готово. Исходных строк: {len(rows)}"] +
                        [f"  {key}: {dest}" for key, dest in outputs.items()]))
        print(outputs["summary"].read_text(encoding="utf-8"))
        print(f"ОСНОВНОЙ ФАЙЛ: {outputs['chipdip']}")
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError, csv.Error) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
