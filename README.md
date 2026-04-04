# MeshCore Remote Firmware Upgrade

Strumento minimale per aggiornare in sicurezza un nodo MeshCore collegato via USB seriale a un Raspberry remoto.

## Obiettivo

Questo progetto è pensato SOLO PER AGGIORNARE FIRMWARE GIA' INSTALLATI su board ESP32 compatibili MeshCore, da terminale Linux (questo progetto è stato testato con successo in un semplice ed economico Raspberry PI0 2W, con a bordo una SDmemory da 32Gb)

Scelte conservative:
- usa la porta seriale stabile sotto `/dev/serial/by-id/` quando disponibile
- verifica la comunicazione con `esptool` prima di toccare la board
- scarica il firmware dal catalogo ufficiale di `https://flasher.meshcore.io`
- sceglie solo firmware di tipo update
- rifiuta file `flash-wipe` e `*-merged.bin`
- scrive il firmware a `0x10000`
- può creare un backup completo della flash prima dell'aggiornamento

## Installazione locale

Dalla cartella del progetto:

```bash
bash install.sh
```

## Installazione da GitHub con un solo comando

Dopo aver pubblicato il repository:

```bash
curl -fsSL https://raw.githubusercontent.com/xpinguinx/meshcore-remote-firmware-upgrade/main/install.sh | bash -s -- --repo TUO-UTENTE/meshcore-remote-firmware-upgrade
```

## Uso

Avvio guidato:

```bash
meshcore-update
```

Solo rilevamento seriali:

```bash
meshcore-update --detect
```

Solo verifica board:

```bash
meshcore-update --verify
```

Autotest installazione:

```bash
meshcore-update --self-test
```

Con porta esplicita:

```bash
meshcore-update --port /dev/serial/by-id/usb-XXXXX
```

Filtro board:

```bash
meshcore-update --board [tipo di board/scheda] (LilyGo, Heltec, ecc.)
```

<img width="619" height="980" alt="board select" src="https://github.com/user-attachments/assets/891981ba-81e3-489d-8e6b-56f34a28ed44" />

<br>

<img width="1320" height="909" alt="version select" src="https://github.com/user-attachments/assets/ef68ad9f-3b7e-4dd4-be10-046bb15b00e3" />



## Requisiti

- Raspberry / Linux con accesso internet
- board ESP32 MeshCore collegata via USB dati
- utente con privilegi `sudo`

## Note di sicurezza

Questo tool non esegue installazioni `wipe` e non fa erase totale della flash.
Per una postazione remota è una scelta voluta, per ridurre il rischio operativo.

Se il catalogo MeshCore per una certa board/versione offre solo immagini `wipe` o `merged`, il tool interrompe la procedura.

## File inclusi

- `install.sh` installer locale e GitHub bootstrap
- `meshcore_updater.py` tool principale
- `requirements.txt` dipendenze Python
- `.gitattributes` forza i file shell/python in LF


