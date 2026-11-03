#!/usr/bin/env python3
"""Convert a grouped KiCad CSV BOM to a ChipDip paste-ready part list.

Requires Python 3.9+. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass
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


def normalize_header(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value).strip().casefold())


def read_csv_table(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
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
    sample = decoded[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    from io import StringIO
    reader = csv.DictReader(StringIO(decoded, newline=""), delimiter=delimiter)
    if reader.fieldnames is None:
        raise ValueError("Пустой CSV")
    columns = {normalize_header(h): h for h in reader.fieldnames if h is not None}
    if "reference" not in columns or "value" not in columns:
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
    rows, _ = read_csv_table(path)
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
                                 "Manufacturer Part Number", "MPN", "PartNumber"))
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
        result.append(BomItem(ref, qty * boards, value, footprint, part, status, reason))
    return result, warnings


def write_csv(path: Path, headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(headers)
        writer.writerows(rows)


def write_outputs(source: Path, items: List[BomItem], warnings: List[str],
                  dest: Path, include_nominals: bool) -> Dict[str, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    paths = {
        "chipdip": dest / f"{stem}_chipdip.txt",
        "review": dest / f"{stem}_review.csv",
        "mapping": dest / f"{stem}_mapping_template.csv",
        "summary": dest / f"{stem}_summary.txt",
    }
    grouped: OrderedDict[str, Tuple[str, int]] = OrderedDict()
    for item in items:
        if not item.part:
            continue
        key = SPACES.sub(" ", item.part).casefold()
        if key in grouped:
            name, qty = grouped[key]
            grouped[key] = (name, qty + item.qty)
        else:
            grouped[key] = (item.part, item.qty)
    with paths["chipdip"].open("w", encoding="utf-8", newline="\n") as stream:
        for part, qty in grouped.values():
            stream.write(f"{part}\t{qty}\n")

    write_csv(paths["review"],
              ["Reference", "Qty", "Value", "Footprint", "PartNumber", "Status", "Reason"],
              [[i.reference, i.qty, i.value, i.footprint, i.part, i.status, i.reason] for i in items])
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
        f"Всего компонентов: {sum(i.qty for i in items)}",
        f"Строк для вставки в ChipDip (после объединения): {len(grouped)}",
        f"Количество включено в список: {sum(i.qty for i in items if i.part)}",
        f"Точные артикулы из BOM: {counts['explicit']} шт.",
        f"Артикулы из сопоставления: {counts['mapped']} шт.",
        f"Кандидаты из Value (проверить): {counts['candidate']} шт.",
        f"Поиск по номиналам (не для слепого заказа): {counts['nominal_search']} шт.",
        f"Требуют подбора артикула: {counts['unresolved']} шт.",
        f"Поиск по номиналам разрешён: {'да' if include_nominals else 'нет'}",
        "ВАЖНО: Value не гарантирует точный MPN. Перед заказом проверьте производителя,",
        "корпус, параметры и допустимость замены для каждой позиции.",
    ]
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
    except Exception as exc:
        raise ValueError("Графический выбор файла недоступен; укажите путь к CSV аргументом") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="KiCad CSV BOM → текстовый список ChipDip")
    parser.add_argument("input", nargs="?", type=Path, help="CSV BOM; без аргумента откроется окно выбора")
    parser.add_argument("--boards", type=int, help="Количество одинаковых плат (перекрывает settings.json)")
    parser.add_argument("--mapping", type=Path, help="CSV с Reference/Value/Footprint/PartNumber")
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
        mapping_ref, mapping_value = load_mapping(mapping)
        items, warnings = convert(rows, mapping_ref, mapping_value, boards,
                                  include_nominals)
        outputs = write_outputs(path, items, warnings, out or path.parent,
                                include_nominals)
        print("\n".join([f"Готово. Исходных строк: {len(rows)}"] +
                        [f"  {key}: {dest}" for key, dest in outputs.items()]))
        print(outputs["summary"].read_text(encoding="utf-8"))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError, csv.Error) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
