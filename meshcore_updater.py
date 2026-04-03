#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from serial.tools import list_ports
except ModuleNotFoundError:
    print(
        "Dipendenza mancante: pyserial. Reinstalla con install.sh o esegui nuovamente l'installer.",
        file=sys.stderr,
    )
    raise SystemExit(2)

BASE_URL = os.environ.get("MESHCORE_BASE_URL", "https://flasher.meshcore.io")
CONFIG_URL = urllib.parse.urljoin(BASE_URL, "/config.json")
RELEASES_URL = urllib.parse.urljoin(BASE_URL, "/releases")

APP_HOME = Path.home() / ".local" / "share" / "meshcore-remote-updater"
FW_DIR = APP_HOME / "firmware"
BK_DIR = APP_HOME / "backups"
LOG_DIR = APP_HOME / "logs"

DEFAULT_BAUD = 115200
USER_AGENT = "meshcore-remote-firmware-upgrade/2.0"


@dataclass
class PortInfo:
    device: str
    stable: str | None
    description: str
    hwid: str
    manufacturer: str | None
    product: str | None
    serial_number: str | None

    @property
    def preferred(self) -> str:
        return self.stable or self.device

    def summary(self) -> str:
        parts = [self.preferred]
        if self.description:
            parts.append(self.description)
        usb = []
        if self.manufacturer:
            usb.append(self.manufacturer)
        if self.product:
            usb.append(self.product)
        if usb:
            parts.append(" / ".join(usb))
        if self.serial_number:
            parts.append(f"SN:{self.serial_number}")
        return " | ".join(parts)


@dataclass
class SelectedFirmware:
    board_name: str
    role_key: str
    role_label: str
    version_name: str
    file_title: str
    file_url: str


def ensure_dirs() -> None:
    for d in (FW_DIR, BK_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def log_path(prefix: str, suffix: str = "log") -> Path:
    ensure_dirs()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return LOG_DIR / f"{prefix}-{stamp}.{suffix}"


def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def stable_links_map() -> dict[str, str]:
    root = Path("/dev/serial/by-id")
    result: dict[str, str] = {}
    if not root.exists():
        return result
    for entry in root.iterdir():
        try:
            target = os.path.realpath(entry)
        except OSError:
            continue
        result[target] = str(entry)
    return result


def detect_ports() -> list[PortInfo]:
    links = stable_links_map()
    ports: list[PortInfo] = []
    for p in list_ports.comports(include_links=True):
        if not (
            p.device.startswith("/dev/ttyUSB")
            or p.device.startswith("/dev/ttyACM")
            or p.device.startswith("/dev/serial/")
        ):
            continue
        real = os.path.realpath(p.device)
        stable = links.get(real)
        ports.append(
            PortInfo(
                device=p.device,
                stable=stable,
                description=p.description or "",
                hwid=p.hwid or "",
                manufacturer=getattr(p, "manufacturer", None),
                product=getattr(p, "product", None),
                serial_number=getattr(p, "serial_number", None),
            )
        )
    dedup: dict[str, PortInfo] = {}
    for port in ports:
        key = os.path.realpath(port.device)
        current = dedup.get(key)
        if current is None:
            dedup[key] = port
        elif current.stable is None and port.stable is not None:
            dedup[key] = port
    return sorted(dedup.values(), key=lambda x: x.preferred)


def choose_port(interactive: bool = True, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    ports = detect_ports()
    if not ports:
        raise SystemExit("Nessuna porta seriale USB trovata. Controlla il cavo USB dati e riprova.")
    if len(ports) == 1 or not interactive:
        return ports[0].preferred

    print("\nPorte seriali rilevate:")
    for idx, port in enumerate(ports, start=1):
        print(f"  {idx}) {port.summary()}")
    while True:
        choice = input(f"Seleziona la porta [1-{len(ports)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(ports):
            return ports[int(choice) - 1].preferred
        print("Scelta non valida.")


def esptool_cmd(*args: str) -> list[str]:
    return [sys.executable, "-m", "esptool", *args]


def run_esptool(port: str, *args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = esptool_cmd("-p", port, "-b", str(DEFAULT_BAUD), *args)
    try:
        return subprocess.run(cmd, text=True, capture_output=capture, check=False)
    except FileNotFoundError:
        raise SystemExit("Impossibile eseguire Python del virtualenv. Reinstalla con install.sh.")


def verify_port(port: str) -> tuple[bool, str]:
    chip = run_esptool(port, "chip-id", capture=True)
    flash = run_esptool(port, "flash-id", capture=True)
    ok = chip.returncode == 0 and flash.returncode == 0
    output = []
    output.append("$ " + " ".join(esptool_cmd("-p", port, "-b", str(DEFAULT_BAUD), "chip-id")))
    output.append(chip.stdout.strip() or chip.stderr.strip())
    output.append("")
    output.append("$ " + " ".join(esptool_cmd("-p", port, "-b", str(DEFAULT_BAUD), "flash-id")))
    output.append(flash.stdout.strip() or flash.stderr.strip())
    return ok, "\n".join(output).strip()


def image_info(bin_path: Path) -> tuple[bool, str]:
    proc = subprocess.run(esptool_cmd("image-info", str(bin_path)), text=True, capture_output=True, check=False)
    return proc.returncode == 0, (proc.stdout.strip() or proc.stderr.strip())


def get_role_label(cfg: dict[str, Any], key: str) -> str:
    role = cfg.get("role", {}).get(key, {})
    title = role.get("title", key)
    subtitle = role.get("subTitle")
    return f"{title} / {subtitle}" if subtitle else title


def file_url(cfg: dict[str, Any], file_name: str) -> str:
    if file_name.startswith("http://") or file_name.startswith("https://"):
        return file_name
    if file_name.startswith("/"):
        return urllib.parse.urljoin(BASE_URL, file_name)
    static_path = cfg.get("staticPath", "/firmware").rstrip("/") + "/"
    return urllib.parse.urljoin(BASE_URL, static_path + file_name)


def build_catalog() -> dict[str, Any]:
    cfg = fetch_json(CONFIG_URL)
    releases = fetch_json(RELEASES_URL)

    def github_versions(role_type: str, files_map: dict[str, str]) -> dict[str, Any]:
        versions: dict[str, Any] = {}
        for rel in releases:
            if rel.get("type") != role_type:
                continue
            version = versions.setdefault(rel["version"], {"notes": rel.get("notes", ""), "files": []})
            for file_type, regex in files_map.items():
                cre = re.compile(regex)
                for f in rel.get("files", []):
                    name = f.get("name", "")
                    if cre.search(name):
                        version["files"].append(
                            {
                                "type": file_type,
                                "name": f.get("url", ""),
                                "title": name,
                            }
                        )
        return {k: v for k, v in versions.items() if v["files"]}

    for device in cfg.get("device", []):
        for firmware in device.get("firmware", []):
            gdef = firmware.get("github")
            if gdef and gdef.get("files"):
                firmware["version"] = github_versions(gdef["type"], gdef["files"])

    cfg["device"] = [
        d for d in cfg.get("device", []) if any((fw.get("version") or {}) for fw in d.get("firmware", []))
    ]
    return cfg


def pick_from_menu(items: list[str], title: str) -> int:
    print(f"\n{title}")
    for idx, item in enumerate(items, start=1):
        print(f"  {idx}) {item}")
    while True:
        value = input(f"Seleziona [1-{len(items)}]: ").strip()
        if value.isdigit() and 1 <= int(value) <= len(items):
            return int(value) - 1
        print("Scelta non valida.")


def list_matching_devices(cfg: dict[str, Any], board_filter: str | None = None) -> list[dict[str, Any]]:
    devices = cfg.get("device", [])
    if not board_filter:
        return devices
    filt = normalize(board_filter)
    return [d for d in devices if filt in normalize(d.get("name", ""))]


def choose_firmware(
    cfg: dict[str, Any],
    board_filter: str | None = None,
    role_filter: str | None = "repeater",
    version_filter: str | None = None,
) -> SelectedFirmware:
    devices = list_matching_devices(cfg, board_filter)
    if not devices:
        raise SystemExit("Nessuna board trovata nel catalogo MeshCore con quel filtro.")

    if len(devices) == 1:
        device = devices[0]
    else:
        idx = pick_from_menu([d["name"] for d in devices], "Board disponibili")
        device = devices[idx]

    firmwares = [fw for fw in device.get("firmware", []) if (fw.get("version") or {})]
    if role_filter:
        rf = normalize(role_filter)
        filtered = []
        for fw in firmwares:
            role_name = fw.get("role", "")
            role_label = get_role_label(cfg, role_name)
            if rf in normalize(role_name) or rf in normalize(role_label):
                filtered.append(fw)
        if filtered:
            firmwares = filtered

    if not firmwares:
        raise SystemExit("Nessun firmware disponibile per questa board.")

    if len(firmwares) == 1:
        fw = firmwares[0]
    else:
        idx = pick_from_menu(
            [f'{x.get("role", "?")} ({get_role_label(cfg, x.get("role", ""))})' for x in firmwares],
            "Ruoli disponibili",
        )
        fw = firmwares[idx]

    versions = sorted((fw.get("version") or {}).keys(), reverse=True)
    if version_filter:
        vf = normalize(version_filter)
        versions = [v for v in versions if vf in normalize(v)]
    if not versions:
        raise SystemExit("Nessuna versione trovata con il filtro richiesto.")

    if len(versions) == 1:
        version_name = versions[0]
    else:
        idx = pick_from_menu(versions, "Versioni disponibili")
        version_name = versions[idx]

    version = fw["version"][version_name]
    file_candidates = version.get("files", [])
    chosen = None
    for f in file_candidates:
        title = (f.get("title") or "").lower()
        if f.get("type") == "flash-update" and title.endswith(".bin"):
            chosen = f
            break
    if chosen is None:
        for f in file_candidates:
            title = (f.get("title") or "").lower()
            if f.get("type") == "flash" and title.endswith(".bin") and "merged" not in title and "wipe" not in title:
                chosen = f
                break
    if chosen is None:
        raise SystemExit(
            "Per questa combinazione board/ruolo/versione non esiste un firmware di aggiornamento sicuro. "
            "Il tool rifiuta i file wipe/merged per evitare danni da remoto."
        )

    return SelectedFirmware(
        board_name=device["name"],
        role_key=fw.get("role", "?"),
        role_label=get_role_label(cfg, fw.get("role", "")),
        version_name=version_name,
        file_title=chosen.get("title", "firmware.bin"),
        file_url=file_url(cfg, chosen.get("name", "")),
    )


def download_file(url: str, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    filename = os.path.basename(urllib.parse.urlparse(url).path) or "meshcore.bin"
    outpath = outdir / filename
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp, open(outpath, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    return outpath


def backup_flash(port: str) -> Path:
    ensure_dirs()
    path = BK_DIR / f"flash-backup-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.bin"
    print(f"\nCreo backup completo in: {path}")
    proc = run_esptool(port, "read-flash", "0", "ALL", str(path))
    if proc.returncode != 0:
        raise SystemExit("Backup fallito. Aggiornamento interrotto per sicurezza.")
    return path


def flash_update(port: str, bin_path: Path) -> None:
    cmd = esptool_cmd(
        "-p",
        port,
        "-b",
        str(DEFAULT_BAUD),
        "--before",
        "default-reset",
        "--after",
        "hard-reset",
        "write-flash",
        "0x10000",
        str(bin_path),
    )
    print("\nEseguo aggiornamento firmware...")
    proc = subprocess.run(cmd, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit("Aggiornamento fallito.")
    print("\nAggiornamento completato.")


def yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    ans = input(prompt + suffix).strip().lower()
    if not ans:
        return default
    return ans in {"y", "yes", "s", "si"}


def cmd_self_test(_args: argparse.Namespace) -> int:
    ensure_dirs()
    failures: list[str] = []
    try:
        import esptool  # noqa: F401
    except Exception as exc:  # pragma: no cover
        failures.append(f"esptool non importabile: {exc}")
    if not detect_ports():
        print("Nessuna seriale USB rilevata. Il tool però è installato correttamente.")
    if failures:
        for item in failures:
            print(item, file=sys.stderr)
        return 1
    print("Autotest OK")
    return 0


def cmd_detect(_args: argparse.Namespace) -> int:
    ports = detect_ports()
    if not ports:
        print("Nessuna porta seriale USB trovata.")
        return 1
    for port in ports:
        print(port.summary())
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    port = choose_port(interactive=False, explicit=args.port)
    ok, output = verify_port(port)
    print(f"Porta: {port}\n")
    print(output)
    return 0 if ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    ensure_dirs()
    print("\nMeshCore Remote Firmware Upgrade")
    print("--------------------------------")
    print("Modalità sicura: solo aggiornamento firmware, niente wipe/merged image.\n")

    port = choose_port(interactive=True, explicit=args.port)
    print(f"Porta selezionata: {port}")

    ok, verify_out = verify_port(port)
    verify_log = log_path("verify")
    verify_log.write_text(verify_out + "\n", encoding="utf-8")
    if not ok:
        print("\nVerifica seriale fallita.\n")
        print(verify_out)
        print(f"\nLog salvato in: {verify_log}")
        return 1

    print("\nVerifica seriale OK.")
    cfg = build_catalog()
    selection = choose_firmware(
        cfg,
        board_filter=args.board,
        role_filter=args.role,
        version_filter=args.version,
    )

    print("\nSelezione firmware:")
    print(f"  Board    : {selection.board_name}")
    print(f"  Ruolo    : {selection.role_key} ({selection.role_label})")
    print(f"  Versione : {selection.version_name}")
    print(f"  File     : {selection.file_title}")
    print(f"  URL      : {selection.file_url}")

    bin_path = download_file(selection.file_url, FW_DIR)
    print(f"\nFirmware scaricato in: {bin_path}")

    ok_info, info_text = image_info(bin_path)
    info_log = log_path("image-info")
    info_log.write_text(info_text + "\n", encoding="utf-8")
    if ok_info:
        print("\nControllo immagine OK.")
    else:
        print("\nAttenzione: esptool non è riuscito a leggere image-info del file.")
        print("Per prudenza l'aggiornamento viene interrotto.")
        print(f"Log salvato in: {info_log}")
        return 1

    if not args.no_backup:
        if yes_no("Vuoi creare un backup completo della flash prima dell'update?", default=True):
            backup_path = backup_flash(port)
            print(f"Backup completato: {backup_path}")
        else:
            print("Backup saltato su richiesta utente.")
    else:
        print("Backup disabilitato da riga di comando (--no-backup).")

    print(
        textwrap.dedent(
            f"""
            Riepilogo finale
              Porta    : {port}
              Board    : {selection.board_name}
              Ruolo    : {selection.role_key} ({selection.role_label})
              Versione : {selection.version_name}
              Bin      : {bin_path}
              Offset   : 0x10000 (update-only)
            """
        ).rstrip()
    )

    if not yes_no("Confermi l'aggiornamento firmware?", default=False):
        print("Operazione annullata.")
        return 0

    flash_update(port, bin_path)
    print("\nVerifica finale:")
    ok2, verify_out2 = verify_port(port)
    print(verify_out2)
    return 0 if ok2 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggiornamento firmware MeshCore da terminale, in modalità sicura.")
    parser.add_argument("--port", help="Porta seriale da usare, es. /dev/serial/by-id/...", default=None)
    parser.add_argument("--board", help="Filtro board, es. LilyGo o Heltec", default=None)
    parser.add_argument("--role", help="Filtro ruolo, default: repeater", default="repeater")
    parser.add_argument("--version", help="Filtro versione, es. v1.14.1", default=None)
    parser.add_argument("--no-backup", help="Salta il backup completo prima dell'update", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="Verifica installazione e dipendenze")

    sub = parser.add_subparsers(dest="cmd")
    p_detect = sub.add_parser("detect", help="Elenca le porte seriali")
    p_detect.add_argument("--port", help="Ignorato in detect", default=None)
    p_detect.set_defaults(func=cmd_detect)

    p_verify = sub.add_parser("verify", help="Verifica la comunicazione con la board")
    p_verify.add_argument("--port", help="Porta seriale da usare, es. /dev/serial/by-id/...", default=None)
    p_verify.set_defaults(func=cmd_verify)

    parser.set_defaults(func=cmd_run)
    return parser


def main() -> int:
    argv = sys.argv[1:]
    if "--detect" in argv:
        argv = ["detect" if x == "--detect" else x for x in argv]
    if "--verify" in argv:
        argv = ["verify" if x == "--verify" else x for x in argv]

    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "self_test", False):
        return cmd_self_test(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
