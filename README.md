# Musik-Import

Neue Musik in den Import-Ordner legen. Die App liest die Dateien, ergänzt fehlende Angaben und verschiebt sie in die Navidrome-Bibliothek.

Ergänzt wird, was fehlt:

- Interpret, Album, Titel, Nummer, Jahr, Genre
- MusicBrainz-IDs
- Original-Cover, eingebettet und als `cover.jpg`
- Songtext in der Datei und, wenn vorhanden, eine `.lrc` daneben

Die Verzeichnisstruktur ist wählbar. Liegt dasselbe Album schon unter dem Interpreten, kommen neue Titel in diesen Ordner. Danach kann Navidrome einen Scan starten und die Titel erscheinen in der Bibliothek.

Unsichere Dateien bleiben im Import. In der Web-UI korrigierst du die Angaben und übernimmst sie. Dateien, die am Ziel schon existieren, landen in `Import/_duplikate` und überschreiben nichts.

## Unraid

Image: `ghcr.io/paulg67/musik-import:latest`

### 1. Vorlage installieren

Im Unraid-Terminal:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/PaulG67/musik-import/main/unraid/install-template.sh)
```

### 2. Container anlegen

1. **Docker → Container hinzufügen**
2. Vorlage **musik-import**
3. **Import** ausserhalb der Navidrome-Bibliothek lassen, zum Beispiel `/mnt/user/appdata/musik-import/import`
4. **Musikbibliothek** auf denselben Share setzen, den Navidrome liest, zum Beispiel `/mnt/user/music`
5. Optional Navidrome-URL, Benutzer und Passwort, damit nach dem Verschieben ein Scan startet
6. **Apply**

Web-UI nur im LAN: `http://UNRAID-IP:8793`

### 3. Update erzwingen

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/PaulG67/musik-import/main/unraid/install-template.sh)
```

Dann **Docker → musik-import → Force Update**.

Wenn die Vorlage neue Felder hat: zusätzlich **Edit → Apply**.

Falls Unraid das Image nicht zieht: [Package musik-import](https://github.com/PaulG67/musik-import/pkgs/container/musik-import) → **Package settings → Change visibility → Public**.

## Nutzung

Alben oder einzelne Dateien in den Import-Ordner kopieren. Die App wartet, bis die Dateigrösse sich nicht mehr ändert, sucht dann bei MusicBrainz und verschiebt sichere Treffer.

Ordner wie `Interpret - Album (2011)` oder `Interpret/Album/01 - Titel.flac` reichen oft schon ohne Tags. Für Dateien ohne brauchbaren Namen hilft ein kostenloser [AcoustID-Key](https://acoustid.org/new-application). `fpcalc` ist im Image enthalten.

In der Web-UI:

- **Eingang:** Vorschlag, Zielpfad, Cover. **Übernehmen** verschiebt mit deinen Korrekturen. **Erneut suchen** fragt noch einmal. **Nicht importieren** legt nach `_aussortiert`.
- **Verlauf:** was verschoben wurde
- **Einstellungen:** Struktur, Cover, Texte, Navidrome

Unterstützte Dateien: MP3, FLAC, Ogg, Opus, M4A.

## Verzeichnisstruktur

| Wert | Beispiel |
| --- | --- |
| `artist_year_album` | `Adele/2011 - 21/01 - Rolling in the Deep.flac` |
| `artist_album` | `Adele/21/01 - Rolling in the Deep.flac` |
| `artist_album_year` | `Adele/21 (2011)/01 - Rolling in the Deep.flac` |
| `artist_album_cd` | `Adele/21/CD2/01 - Rolling in the Deep.flac` |
| `custom` | eigenes Muster in der Web-UI |

Platzhalter: `{albumartist}` `{artist}` `{album}` `{year}` `{genre}` `{disc}` `{discfolder}` `{track}` `{title}`

`{discfolder}` ist nur bei mehreren CDs gesetzt. Ein leeres Jahr fällt aus dem Ordnernamen weg.

## Variablen

| Variable | Default | Bedeutung |
| --- | --- | --- |
| `PORT` | `8793` | Web-UI |
| `IMPORT_DIR` | `/import` | Ablage für neue Musik |
| `MUSIC_DIR` | `/music` | Navidrome-Bibliothek |
| `CONFIG_DIR` | `/config` | Einstellungen und Verlauf |
| `LAYOUT` | `artist_year_album` | Startstruktur, bis die Web-UI speichert |
| `NAVIDROME_URL` | leer | zum Beispiel `http://192.168.1.10:4533` |
| `NAVIDROME_USER` / `NAVIDROME_PASSWORD` | leer | für `startScan` |
| `ACOUSTID_API_KEY` | leer | optional, Fingerabdruck |
| `PUID` / `PGID` | `99` / `100` | Unraid nobody/users |
| `TZ` | `Europe/Zurich` | Zeitzone |

Was du in der Web-UI speicherst, gilt danach vor den Variablen. Die drei Ordner kommen immer aus dem Container.

## Lokal

```bash
docker compose up -d --build
```

Lege Dateien nach `./import`. Die Bibliothek liegt in `./music`.
