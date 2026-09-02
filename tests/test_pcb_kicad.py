"""KiCad 工具链定位单测。

只测「怎么找」和「找不到时怎么报错」，不测 KiCad 自身的行为。
"""
import unittest
from pathlib import Path

from hdc.pcb import kicad


def _blind_env(tmp: str) -> dict:
    """一个「什么都找不到」的环境：PATH 为空目录，各安装根也指向空目录。"""
    return {"PATH": tmp, "LOCALAPPDATA": tmp, "PROGRAMFILES": tmp,
            "PROGRAMFILES(X86)": tmp, "HOME": tmp}


class TestFindCli(unittest.TestCase):
    def test_env_override_wins(self):
        fake = Path(__file__).resolve()          # 任何存在的文件都行
        env = _blind_env(str(fake.parent)) | {kicad.CLI_ENV: str(fake)}
        self.assertEqual(kicad.find_cli(env), fake)

    def test_missing_override_is_ignored(self):
        with self.subTest("指向不存在的路径时不应假装找到"):
            env = _blind_env(str(Path(__file__).parent)) | {
                kicad.CLI_ENV: str(Path(__file__).parent / "nope.exe")}
            self.assertIsNone(kicad.find_cli(env))

    def test_returns_none_when_nothing_is_installed(self):
        env = _blind_env(str(Path(__file__).resolve().parent))
        self.assertIsNone(kicad.find_cli(env))
        self.assertIsNone(kicad.find_python(env))

    def test_require_cli_error_names_the_env_var(self):
        env = _blind_env(str(Path(__file__).resolve().parent))
        with self.assertRaises(kicad.KicadError) as ctx:
            kicad.require_cli(env)
        self.assertIn(kicad.CLI_ENV, str(ctx.exception))


class TestRealInstall(unittest.TestCase):
    """本机装了 KiCad 时才跑：确认找到的东西真的能用。"""

    @unittest.skipUnless(kicad.find_cli(), "未找到 kicad-cli")
    def test_cli_reports_a_version(self):
        out = kicad.run(["version"]).stdout
        self.assertRegex(out.strip(), r"^\d+\.\d+")

    @unittest.skipUnless(kicad.find_python(), "未找到 KiCad 自带 Python")
    def test_bundled_python_can_import_pcbnew(self):
        proc = kicad.run_python(["-c", "import pcbnew; print(pcbnew.GetBuildVersion())"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip(), proc.stderr)


if __name__ == "__main__":
    unittest.main()
