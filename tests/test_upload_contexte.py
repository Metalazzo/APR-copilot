"""Test offline du handler d'upload de contexte (API NiceGUI 3.x : e.file).

python tests/test_upload_contexte.py   (~2 s)
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILURES = []


class FakeFile:
    name = "ctx_test.txt"

    async def read(self) -> bytes:
        return "CONTEXT-TEST 25kV".encode("utf-8")


class FakeEvent:
    file = FakeFile()


def main():
    import gui

    async def run():
        gui.context_input.value = ""
        try:
            await gui.on_context_upload(FakeEvent())   # ui.notify leve hors contexte NiceGUI : ignore
        except RuntimeError:
            pass
        return gui.context_input.value

    value = asyncio.run(run())
    try:
        assert "CONTEXT-TEST 25kV" in value, value
        print("[OK]   e.file.read() async -> champ contexte rempli")
    except AssertionError:
        import traceback
        traceback.print_exc()
        print("[FAIL] e.file.read() async")
        FAILURES.append("upload")

    # ancienne API : doit echouer proprement (l'attribut n'existe plus)
    class OldEvent:
        pass
    async def run_old():
        try:
            await gui.on_context_upload(OldEvent())
            return "no-exception"
        except Exception as exc:
            return f"handled: {exc}"
    res = asyncio.run(run_old())
    try:
        assert res.startswith("handled:"), res
        print("[OK]   evenement sans e.file -> message d'erreur propre")
    except AssertionError:
        import traceback
        traceback.print_exc()
        print("[FAIL] evenement sans e.file")
        FAILURES.append("old-api")

    if not FAILURES:
        print("\nTEST UPLOAD CONTEXTE : TOUT PASSE")
    else:
        print(f"\n{len(FAILURES)} echec(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()
