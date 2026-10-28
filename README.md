# ch-autochart

Turn an audio file into a ready-to-play **Clone Hero** song folder.

```
python autochart.py "My Song.mp3" --artist "My Band" --title "My Song"
```

produces

```
My Band - My Song/
├── notes.chart   guitar chart: Expert, Hard, Medium, Easy + tempo map, sections, star power
├── song.ogg      your audio, with a short silent lead-in added
├── song.ini      name, artist, album, length, preview start, difficulty rating
└── album.png     (only with --album-art)
```

Copy that folder into Clone Hero's `Songs` folder, then in-game go to
**Settings → General → Scan Songs**.

The chart is a strong starting point, not a hand-authored chart. Open
`notes.chart` in [Moonscraper](https://github.com/FireFox2000000/Moonscraper-Chart-Editor/releases)
to clean it up: the tempo map and note timing are usually spot on, and the
fret choices and note selection are where a human pass helps most.

## Install

You need Python 3.9 or newer.

```
pip install -r requirements.txt
```

[ffmpeg](https://ffmpeg.org/download.html) on your PATH is recommended. Without
it, mp3 / ogg / wav / flac still work; with it you can also use m4a, opus, wma
and video files, and the song's own tags (title, artist, album, year) are read
automatically.

## Usage

```
python autochart.py SONG [options]
```

| Option | What it does |
| --- | --- |
| `--title`, `--artist`, `--album`, `--genre`, `--year`, `--charter` | Metadata. Defaults come from the file's tags, then its file name. |
| `--album-art IMG` | Crops and resizes any image to a 512×512 `album.png`. |
| `-o DIR` | Where to create the song folder (default: current folder). Point it at your Clone Hero `Songs` folder to skip copying. |
| `--force` | Overwrite an existing song folder. |
| `--instrument guitar\|bass` | Chart lead guitar (default) or bass. |
| `--difficulties expert,hard` | Only generate some difficulties. |
| `--density 1.3` | More (above 1) or fewer (below 1) notes. |
| `--stem guitar.wav` | Analyze an isolated guitar/bass track instead of the full mix (see below). |
| `--triplets` | Allow triplet timing, for shuffles and swing. |
| `--all-onsets` | Also chart hits with no pitch (drums). Off by default. |
| `--no-chords`, `--no-sustains` | Single notes only / no held notes. |
| `--min-sustain 0.5` | Shortest sustain kept, in beats. |
| `--star-power-every 8` | Bars between star power phrases (0 = none). |
| `--bpm 140` | Force a constant tempo, if detection picks the wrong one. |
| `--bpm-hint 140` | Steer tempo detection without forcing it (useful if it picks half or double time). |
| `--tempo-map auto\|constant\|variable` | `constant` = one BPM (songs recorded to a click); `variable` = follow tempo drift (live recordings). `auto` decides. |
| `--first-downbeat 2.31` | Seconds into the audio where a "beat 1" falls, if the bar lines come out shifted. |
| `--time-signature 3` | Beats per bar (default 4). |
| `--lead-in 1.5` | Minimum seconds of silence before the music starts. |

### Examples

```
# Straight into your Clone Hero songs folder (Windows)
python autochart.py "Song.mp3" -o "%USERPROFILE%\Documents\Clone Hero\Songs"

# macOS / Linux
python3 autochart.py song.flac -o ~/Clone\ Hero/Songs     # Linux: ~/.clonehero/Songs

# Tempo came out at half speed? Give it a hint
python autochart.py song.mp3 --bpm-hint 170

# Easier, sparser chart with no chords
python autochart.py song.mp3 --density 0.7 --no-chords
```

### Getting much better charts with a stem

On a full band mix the charter hears everything: drums, bass, vocals and
guitar. If you separate the guitar first, the notes and frets follow the
guitar part much more closely. [Demucs](https://github.com/adefossez/demucs)
does this well:

```
pip install demucs
demucs -n htdemucs_6s "Song.mp3"          # 6-stem model includes a guitar stem
python autochart.py "Song.mp3" --stem "separated/htdemucs_6s/Song/guitar.wav"
```

The full mix is still what plays in-game; the stem is only used for analysis.
Use the bass stem with `--instrument bass` for a bass chart.

## How it works

1. **Tempo map.** Beats are tracked, re-timed on a fine (~3 ms) onset
   envelope, and fitted to one constant BPM when the song is steady, or to a
   BPM marker wherever the tempo drifts. Beat 1 of each bar is found from
   kick/bass hits and from where the chords change. A silent lead-in is added
   to the audio so the chart starts cleanly on a downbeat.
2. **Notes.** Onsets come from both the full mix and its harmonic part (so soft
   picked notes aren't drowned out by drums), are pinned to the exact attack,
   and snapped to a 1/16 (or 1/12) grid. Hits with no pitched content, like hi-hats,
   are skipped.
3. **Frets.** For each note, the pitch that just started is estimated from a
   constant-Q spectrogram with harmonic summation. Frets then follow the
   melody's contour like a human charter: up the neck when the pitch rises,
   the same fret for a repeated note, bigger jumps for bigger leaps. A Viterbi
   search plans ahead so a rising run starts low enough to keep rising.
4. **Sustains** come from how long each note keeps ringing; **chords** from the
   strongest accents.
5. **Difficulties.** Hard, Medium and Easy are subsets of Expert that keep the
   strongest, most on-beat notes, with wider spacing and fewer frets
   (Medium: green–blue, Easy: green–yellow).
6. **Song structure.** Bars are grouped by harmony and timbre into sections;
   repeated loud parts become "Chorus", other repeats "Verse", plus "Intro",
   "Bridge" and "Outro". Star power phrases are placed every few bars and the
   song preview starts at the first chorus.

## Testing

`tests/` synthesizes songs whose every note is known and checks the generated
chart against them:

```
python tests/make_test_song.py test.wav --bpm 128               # or --drift 9 for a live-feel tempo
python autochart.py test.wav --artist Test --title Test
python tests/check_chart.py "Test - Test" test.wav
```

Results on the bundled test songs:

| Test | Tempo map | Beat sync | Bar lines | Melody notes caught (Expert) |
| --- | --- | --- | --- | --- |
| 128 BPM, 1 min | 128.00 BPM, constant | 0.5 ms median | 100% | 195 / 196 |
| 152 BPM, 4.8 min | 152.00 BPM, constant | 0.8 ms median | 100% | 1222 / 1232 |
| 112 → 121 BPM drift | 10 markers, variable | 5.1 ms median | 100% | 193 / 196 |

Generated folders also pass [scan-chart](https://github.com/Geomitron/scan-chart)
(the parser Chorus Encore uses to match Clone Hero) as playable with no chart
issues.

## Limitations

- It charts what it hears in the mix; without `--stem`, bass lines, vocals and
  keyboard parts can end up in a guitar chart.
- Section names are educated guesses from repetition and loudness.
- No HOPO/tap forcing, open notes, solos or lyrics. Clone Hero's automatic HOPOs
  apply (fast notes on different frets become HOPOs).
- Songs with heavy rubato or free-time intros may need tempo touch-ups in
  Moonscraper.
- Chart only songs you have the rights to use.
