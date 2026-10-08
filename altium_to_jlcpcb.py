from abc import ABC, abstractmethod
import csv
from pathlib import Path
import re
import sys
from typing import Dict, List, Optional, Protocol, Tuple


# =====================================================================
# 1. Domain Models & Abstractions
# =====================================================================

class IFileDetector(ABC):
    """Abstract Strategy for detecting file type from path and content."""

    @abstractmethod
    def can_handle(self, file_path: Path, lines: List[str]) -> bool:
        """Return True if this detector recognizes the file."""
        pass

    @abstractmethod
    def get_file_type(self) -> str:
        """Return file type identifier."""
        pass


class IConverter(ABC):
    """Abstract Converter Strategy."""

    @abstractmethod
    def convert(self, lines: List[str]) -> Tuple[List[str], List[Dict[str, str]]]:
        """
        Parses input file lines and returns:
        (output_fieldnames, list_of_row_dictionaries)
        """
        pass


class ICommand(ABC):
    """Command Pattern Interface."""

    @abstractmethod
    def execute(self) -> Optional[Path]:
        """Executes the command and returns output file Path on success."""
        pass


# =====================================================================
# 2. File Detectors & Content Parsers
# =====================================================================

def detect_file_lines(file_path: Path) -> List[str]:
    """Reads file attempting common encodings and returns non-empty stripped lines."""
    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin1"]
    content = None
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                content = f.read()
                break
        except UnicodeDecodeError:
            continue

    if content is None:
        raise ValueError(f"Could not decode file {file_path} with supported encodings.")

    return [line for line in content.splitlines() if line.strip()]


class PickAndPlaceDetector(IFileDetector):
    """Detects Altium Pick and Place / CPL files."""

    def can_handle(self, file_path: Path, lines: List[str]) -> bool:
        # Check filename heuristic
        name_lower = file_path.name.lower()
        if "pick" in name_lower or "cpl" in name_lower or "place" in name_lower:
            return True

        # Check content headers or metadata
        preview = "\n".join(lines[:25]).lower()
        if "pick and place" in preview or "center-x" in preview or "mid x" in preview:
            return True
        return False

    def get_file_type(self) -> str:
        return "cpl"


class BomDetector(IFileDetector):
    """Detects Altium Bill of Materials (BOM) files."""

    def can_handle(self, file_path: Path, lines: List[str]) -> bool:
        name_lower = file_path.name.lower()
        if "bom" in name_lower or "bill of materials" in name_lower:
            return True

        # Check header row in first few lines
        for line in lines[:10]:
            lower_line = line.lower()
            if "designator" in lower_line and ("footprint" in lower_line or "quantity" in lower_line or "libref" in lower_line):
                return True
        return False

    def get_file_type(self) -> str:
        return "bom"


# =====================================================================
# 3. Concrete Converters
# =====================================================================

class BomConverter(IConverter):
    """
    Converts Altium BOM CSV -> JLCPCB BOM CSV.
    Target Columns: Comment, Designator, Footprint, JLCPCB Part #
    """

    TARGET_FIELDS = ["Comment", "Designator", "Footprint", "JLCPCB Part #"]

    def convert(self, lines: List[str]) -> Tuple[List[str], List[Dict[str, str]]]:
        # Locate CSV header line
        header_idx = -1
        for idx, line in enumerate(lines[:15]):
            line_lower = line.lower()
            if "designator" in line_lower:
                header_idx = idx
                break

        if header_idx == -1:
            raise ValueError("Could not find header row containing 'Designator' in BOM file.")

        reader = csv.DictReader(lines[header_idx:])
        field_map = {name.strip().lower(): name for name in reader.fieldnames if name}

        def get_val(row: Dict[str, str], *candidates: str) -> str:
            for cand in candidates:
                cand_lower = cand.lower()
                if cand_lower in field_map:
                    val = row.get(field_map[cand_lower], "")
                    if val is not None and val.strip():
                        return val.strip()
            return ""

        converted_rows = []
        for row in reader:
            if not any(v.strip() for v in row.values() if v):
                continue

            comment = get_val(row, "Name", "Comment", "LibRef", "Description")
            designator = get_val(row, "Designator", "Designators", "Ref", "RefDes")
            footprint = get_val(row, "Footprint", "Package")
            jlc_part = get_val(
                row,
                "JLCPCB Part #",
                "JLCPCB Part #(optional)",
                "JLCPCB Part #（optional）",
                "LCSC Part #",
                "LCSC Part",
                "LCSC",
                "LCSC#",
            )

            converted_rows.append({
                "Comment": comment,
                "Designator": designator,
                "Footprint": footprint,
                "JLCPCB Part #": jlc_part,
            })

        return self.TARGET_FIELDS, converted_rows


class PickAndPlaceConverter(IConverter):
    """
    Converts Altium Pick and Place CSV -> JLCPCB CPL CSV.
    Target Columns: Designator, Mid X, Mid Y, Layer, Rotation
    """

    TARGET_FIELDS = ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"]

    def convert(self, lines: List[str]) -> Tuple[List[str], List[Dict[str, str]]]:
        # Find header line (skipping Altium metadata banner lines)
        header_idx = -1
        for idx, line in enumerate(lines[:30]):
            line_lower = line.lower()
            if "designator" in line_lower and ("center-x" in line_lower or "mid x" in line_lower or "layer" in line_lower):
                header_idx = idx
                break

        if header_idx == -1:
            raise ValueError("Could not find table header row in Pick and Place file.")

        reader = csv.DictReader(lines[header_idx:])
        field_map = {name.strip().lower(): name for name in reader.fieldnames if name}

        def get_val(row: Dict[str, str], *candidates: str) -> str:
            for cand in candidates:
                cand_lower = cand.lower()
                # Exact or substring match
                for col_key, original_col in field_map.items():
                    if cand_lower == col_key or cand_lower in col_key:
                        val = row.get(original_col, "")
                        if val is not None and val.strip():
                            return val.strip()
            return ""

        converted_rows = []
        for row in reader:
            if not any(v.strip() for v in row.values() if v):
                continue

            designator = get_val(row, "designator", "ref")
            mid_x = get_val(row, "center-x", "mid x", "mid-x", "ref-x", "pad-x", "x")
            mid_y = get_val(row, "center-y", "mid y", "mid-y", "ref-y", "pad-y", "y")
            layer = get_val(row, "layer")
            rotation = get_val(row, "rotation", "rot")

            # Format Layer: Altium uses TopLayer / BottomLayer -> Top / Bottom
            layer_lower = layer.lower()
            if "top" in layer_lower:
                layer = "Top"
            elif "bottom" in layer_lower or "bot" in layer_lower:
                layer = "Bottom"

            converted_rows.append({
                "Designator": designator,
                "Mid X": mid_x,
                "Mid Y": mid_y,
                "Layer": layer,
                "Rotation": rotation,
            })

        return self.TARGET_FIELDS, converted_rows


# =====================================================================
# 4. IoC Container (Inversion of Control)
# =====================================================================

class ConverterContainer:
    """
    Inversion of Control (IoC) Container.
    Registers and resolves Detectors and Converters.
    """

    def __init__(self):
        self._detectors: List[IFileDetector] = []
        self._converters: Dict[str, IConverter] = {}

    def register_detector(self, detector: IFileDetector) -> None:
        self._detectors.append(detector)

    def register_converter(self, file_type: str, converter: IConverter) -> None:
        self._converters[file_type.lower()] = converter

    def resolve_type(self, file_path: Path, lines: List[str]) -> str:
        for detector in self._detectors:
            if detector.can_handle(file_path, lines):
                return detector.get_file_type()
        # Default fallback: inspect columns
        for line in lines[:15]:
            l = line.lower()
            if "center-x" in l or "mid x" in l:
                return "cpl"
        return "bom"

    def resolve_converter(self, file_type: str) -> IConverter:
        converter = self._converters.get(file_type.lower())
        if not converter:
            raise KeyError(f"No converter registered for file type '{file_type}'.")
        return converter


# =====================================================================
# 5. Command Pattern Implementation
# =====================================================================

class ConvertFileCommand(ICommand):
    """
    Command that encapsulates converting a single file to JLCPCB format.
    """

    def __init__(self, input_path: Path, container: ConverterContainer):
        self._input_path = input_path
        self._container = container

    def execute(self) -> Optional[Path]:
        path = self._input_path.resolve()
        if not path.is_file():
            print(f"[!] File not found: {path}")
            return None

        if path.suffix.lower() != ".csv":
            print(f"[!] Skipping non-CSV file: {path.name}")
            return None

        if path.stem.endswith("JLCSMT"):
            print(f"[i] Skipping already converted file: {path.name}")
            return None

        lines = detect_file_lines(path)
        file_type = self._container.resolve_type(path, lines)
        converter = self._container.resolve_converter(file_type)

        output_path = path.parent / f"{path.stem}JLCSMT.csv"
        fieldnames, rows = converter.convert(lines)

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            writer.writerows(rows)

        type_label = "BOM" if file_type == "bom" else "CPL / Pick & Place"
        print(f" [+] Detected {type_label} -> Output: {output_path.name}")
        return output_path


class BatchConvertCommand(ICommand):
    """
    Command to convert all CSV files in a given directory or target path.
    """

    def __init__(self, target_path: Path, container: ConverterContainer):
        self._target_path = target_path
        self._container = container

    def execute(self) -> Optional[Path]:
        path = self._target_path.resolve()
        if not path.exists():
            print(f"[!] Path does not exist: {path}")
            return None

        if path.is_file():
            cmd = ConvertFileCommand(path, self._container)
            return cmd.execute()
        elif path.is_dir():
            csv_files = [f for f in path.glob("*.csv") if not f.stem.endswith("JLCSMT")]
            if not csv_files:
                print(f"[i] No CSV files found to convert in: {path}")
                return None

            print(f"Processing {len(csv_files)} CSV file(s) in {path}:")
            for f in csv_files:
                cmd = ConvertFileCommand(f, self._container)
                cmd.execute()
            return path
        return None


# =====================================================================
# 6. Command Invoker & Application Service
# =====================================================================

class ConverterApplication:
    """
    Main Application runner that wires IoC container and dispatches commands.
    """

    def __init__(self):
        self.container = ConverterContainer()
        self._configure_ioc()

    def _configure_ioc(self) -> None:
        """Configures IoC container dependencies."""
        # Detectors (order matters for priority)
        self.container.register_detector(PickAndPlaceDetector())
        self.container.register_detector(BomDetector())

        # Converters
        self.container.register_converter("bom", BomConverter())
        self.container.register_converter("cpl", PickAndPlaceConverter())

    @staticmethod
    def clean_path(raw_path: str) -> str:
        """Cleans quotes from drag-and-drop paths in Windows terminal."""
        s = raw_path.strip()
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            s = s[1:-1].strip()
        return s

    def run_cli(self, args: List[str]) -> None:
        """Runs batch command on files passed as arguments or starts interactive mode."""
        if len(args) > 0:
            for arg in args:
                clean_p = Path(self.clean_path(arg))
                cmd = BatchConvertCommand(clean_p, self.container)
                cmd.execute()
            print("\nDone!")
            input("Press Enter to exit...")
            return

        # Interactive Mode
        print("=" * 65)
        print("     Altium to JLCPCB Converter (BOM & Pick and Place)")
        print("=" * 65)
        print(" Instructions:")
        print("  - Drag & drop a .csv file or folder into this window, OR")
        print("  - Paste the path to a .csv file or directory containing .csv files")
        print("  - Supports BOMs and Pick & Place (CPL) files automatically")
        print("=" * 65)

        while True:
            try:
                user_input = input("\nEnter file or folder path (or 'q' to quit): ").strip()
            except (KeyboardInterrupt, EOFError):
                break

            if not user_input:
                continue
            if user_input.lower() in ("q", "quit", "exit"):
                break

            target_path = Path(self.clean_path(user_input))
            cmd = BatchConvertCommand(target_path, self.container)
            cmd.execute()

        print("\nGoodbye!")


# =====================================================================
# Main Entry Point
# =====================================================================

def main():
    app = ConverterApplication()
    app.run_cli(sys.argv[1:])


if __name__ == "__main__":
    main()
