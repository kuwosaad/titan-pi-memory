"""Codebase verification for technical facts extracted from conversations."""
import re
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class VerificationResult:
    verified: bool
    confidence: float
    evidence: str
    method: str


class CodebaseVerifier:
    """Verify technical claims against actual codebase."""

    def __init__(self, codebase_root: Optional[str] = None):
        self.root = Path(codebase_root) if codebase_root else Path.cwd().resolve()
        self.cache: Dict[str, VerificationResult] = {}

    def is_code_claim(self, memory_text: str) -> bool:
        """Check if memory text appears to be a technical claim about code."""
        code_patterns = [
            r"\bfunction\b", r"\bclass\b", r"\bmethod\b",
            r"\bAPI\b", r"\bendpoint\b", r"\binterface\b",
            r"\bdef\s+\w+\(", r"class\s+\w+",
            r"\w+\(\w+\)", r"\.\w+\(",
            r"import\s+\w+", r"from\s+\w+\s+import",
        ]
        text_lower = memory_text.lower()
        return any(re.search(pattern, text_lower, re.IGNORECASE) for pattern in code_patterns)

    def verify_function_exists(self, function_name: str) -> Tuple[bool, str]:
        """Check if a function exists in the codebase."""
        pattern = rf"def\s+{re.escape(function_name)}\s*\("
        found_files = []
        for py_file in self.root.rglob("*.py"):
            try:
                content = py_file.read_text(encoding='utf-8')
                if re.search(pattern, content):
                    found_files.append(str(py_file.relative_to(self.root)))
            except Exception:
                continue
        if found_files:
            return True, f"Found in: {', '.join(found_files[:3])}"
        return False, "Function not found in codebase"

    def verify_class_exists(self, class_name: str) -> Tuple[bool, str]:
        """Check if a class exists in the codebase."""
        pattern = rf"class\s+{re.escape(class_name)}"
        found_files = []
        for py_file in self.root.rglob("*.py"):
            try:
                content = py_file.read_text(encoding='utf-8')
                if re.search(pattern, content):
                    found_files.append(str(py_file.relative_to(self.root)))
            except Exception:
                continue
        if found_files:
            return True, f"Found in: {', '.join(found_files[:3])}"
        return False, "Class not found in codebase"

    def verify_import_exists(self, import_name: str) -> Tuple[bool, str]:
        """Check if an import/module exists."""
        module_path = self.root / f"{import_name.replace('.', '/')}.py"
        if module_path.exists():
            return True, f"Local module: {import_name}"
        package_path = self.root / import_name.replace('.', '/')
        if package_path.exists() and package_path.is_dir():
            init_file = package_path / "__init__.py"
            if init_file.exists():
                return True, f"Local package: {import_name}"
        common_modules = ["typing", "pathlib", "json", "yaml", "datetime", "pydantic"]
        if any(import_name.startswith(m) for m in common_modules):
            return True, f"Standard library: {import_name}"
        return (False, "Import not found (external module validation not implemented)")

    def verify_memory(self, memory_text: str) -> VerificationResult:
        """Main verification entry point."""
        cache_key = memory_text.lower().strip()
        if cache_key in self.cache:
            return self.cache[cache_key]
        if not self.is_code_claim(memory_text):
            result = VerificationResult(
                verified=False,
                confidence=0.0,
                evidence="Not a code claim - verification skipped",
                method="skip"
            )
        else:
            result = self._verify_code_claim(memory_text)
        self.cache[cache_key] = result
        return result

    def _verify_code_claim(self, text: str) -> VerificationResult:
        """Attempt to verify a specific code claim."""
        func_match = re.search(r"function\s+(\w+)|(\w+)\s*\(\s*\)", text)
        if func_match:
            func_name = func_match.group(1) or func_match.group(2)
            exists, evidence = self.verify_function_exists(func_name)
            return VerificationResult(
                verified=exists,
                confidence=0.9 if exists else 0.2,
                evidence=evidence,
                method="function_search"
            )
        class_match = re.search(r"class\s+(\w+)", text)
        if class_match:
            class_name = class_match.group(1)
            exists, evidence = self.verify_class_exists(class_name)
            return VerificationResult(
                verified=exists,
                confidence=0.9 if exists else 0.2,
                evidence=evidence,
                method="class_search"
            )
        import_match = re.search(r"import\s+(\S+)|from\s+(\S+)", text)
        if import_match:
            import_name = import_match.group(1) or import_match.group(2)
            exists, evidence = self.verify_import_exists(import_name)
            return VerificationResult(
                verified=exists,
                confidence=0.8 if exists else 0.3,
                evidence=evidence,
                method="import_search"
            )
        return VerificationResult(
            verified=False,
            confidence=0.0,
            evidence="Claim type not supported for automatic verification",
            method="none"
        )


_verifier_instance: Optional[CodebaseVerifier] = None


def get_verifier() -> CodebaseVerifier:
    """Get or create the global verifier instance."""
    global _verifier_instance
    if _verifier_instance is None:
        _verifier_instance = CodebaseVerifier()
    return _verifier_instance
