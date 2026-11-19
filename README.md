# ch-autochart

Turn any song into a ready-to-play **Clone Hero** chart that follows the
**guitar**, even in a full band mix.

The song is split into its instruments (guitar, bass, drums, vocals, piano and
"other") with [Demucs](https://github.com/adefossez/demucs), the guitar's notes
and chords are transcribed with Spotify's
[Basic Pitch](https://github.com/spotify/basic-pitch), and the chart is
written from the guitar alone. Where the guitar sits out, the chart hands the
part to whatever is carrying the song instead, the way Guitar Hero charts the
piano intro or the organ break: the keys, a synth, a harmonica. Where the
guitar is buried under the rest of the band, it borrows the pattern from a
clearer repeat of the same part, the way a human charter would.

There are three ways to use it:

- **The Windows program** (easiest): download
  [`ch-autochart-setup.exe`](https://github.com/HarrisonKeyser/ch-autochart/releases/latest/download/ch-autochart-setup.exe),
  run it, and open ch-autochart from the Start menu. Nothing else to install.
- **The studio from source** (`python app.py`): a local web app where you upload a song,
  listen to each separated instrument, see and repaint which one the chart
  follows bar by bar, preview the chart on a scrolling highway, add album art,
  and download the finished song.
- **The command line** (`python autochart.py song.mp3`): the same engine in one
  command, for batches and scripts.

Either way you get a song folder:

```
My Band - My Song/
├── notes.chart   Expert, Hard, Medium and Easy, with tempo map, sections and star power
├── song.ogg      the song (or everything except the guitar, with "split audio")
├── guitar.ogg    the charted part on its own, so it drops out when you miss (optional)
├── song.ini      name, artist, album, year, length, preview start, difficulty
└── album.png     album art (optional)
```

Put that folder in Clone Hero's `Songs` folder and choose
**Settings → General → Scan Songs**.

## Install on Windows

1. Download [`ch-autochart-setup.exe`](https://github.com/HarrisonKeyser/ch-autochart/releases/latest/download/ch-autochart-setup.exe)
   from the [latest release](https://github.com/HarrisonKeyser/ch-autochart/releases/latest).
2. Run it. Windows may say "Windows protected your PC", because the installer
   isn't code-signed: click **More info**, then **Run anyway**. It installs for
   your user only, with no admin prompt.
3. Open **ch-autochart** from the Start menu.

It needs Windows 10 or 11 (64-bit). Everything it needs is included: Python,
the separation and transcription models, and ffmpeg. The first time you finish a chart, point it at
your Clone Hero Songs folder (if it didn't find it already) and charts go
straight into the game from then on. Uninstall from Settings → Apps.

Each installer is built and tested by GitHub on a clean Windows machine (see
`.github/workflows/windows-installer.yml`): it charts a test band song with the
real separation model and scores it (plus a song built from recorded
instrument samples, scored section by section), then installs the program
silently and runs its self-test before anything is published. To publish a new version:
on GitHub, open **Releases → Draft a new release**, type a new tag such as
`v2.0.1`, and click **Publish release**. About 40 minutes later the installer
is attached to it, and the website's download button serves the new version.

### Updates

When the installed program opens, it asks GitHub whether a newer release
exists (an anonymous request for the latest release; nothing about you or
your songs is sent). If there is one, a bar at the top offers **Install
update**: it downloads the new installer, closes the program, installs over
the old version (keeping your settings, Songs folder and cached songs), and
reopens it. A release only shows up once its installer has finished building.

The version number comes from the release's tag at build time, so releasing
`v2.0.1` produces a program that reports 2.0.1; there's nothing to edit by
hand. Running from source never checks. To turn the check off, set
`"check_updates": false` in `%LOCALAPPDATA%\ch-autochart\settings.json`.

## Install from source

You need Python 3.9 or newer.

```
pip install -r requirements.txt
```

That includes Demucs and PyTorch (a large download). The first time a song is
separated, Demucs downloads its model once. For note transcription, get the
Basic Pitch model (230 KB) with `python packaging/fetch_model.py`, or
`pip install basic-pitch`; without it, a simpler built-in pitch detector is
used and chords come out less consistent. Separation takes a few
minutes per song on a regular CPU and well under a minute with an NVIDIA GPU;
the result is cached, so re-charting the same song is quick. Without Demucs
everything still works, but the chart follows the full mix.

[ffmpeg](https://ffmpeg.org/download.html) on your PATH is recommended: it adds
m4a, opus and video files and reads the song's tags (title, artist, album, year).

## The studio

```
python app.py
```

Your browser opens on `http://127.0.0.1:8765`. Everything runs on your own
computer; nothing is uploaded anywhere.

1. **Upload** a song. Already have stems from Demucs, UVR or Moises? Add them
   under "I already have separated stems" and the separation step is skipped.
2. **Wait** while it separates the instruments, finds the beat and listens to
   each instrument. Progress is shown step by step.
3. **Review** in the studio:
   - one waveform per instrument, with mute and solo, play/pause (space),
     click-to-jump and zoom;
   - the *chart plan*: which instrument the chart follows in every bar,
     highlighted on that instrument's track. **Drag across a track** to chart
     that passage from it, or drag across a highlighted stretch to clear it and
     leave a rest. Reset to the recommendation at any time;
   - a chart preview under the tracks and a scrolling highway beside them, in
     sync with playback, for each difficulty.
   - **Notes** on an instrument's track shows what was heard there as a piano
     roll (every note of each chord, lighting up as it plays), so you can
     check the transcription against the audio. **Save notes as MIDI** saves
     it for MuseScore or any music program.
4. **Approve**, then add album art, title, artist, album, year, genre and
   charter. Choose difficulties and whether the charted part gets its own audio
   track. If your Clone Hero songs folder is found, it can be installed there
   directly.
5. **Download** the finished song as a .zip.

`python app.py --port 9000` picks another port; `--no-browser` skips opening a
window.

## The command line

```
python autochart.py SONG [options]
```

| Option | What it does |
| --- | --- |
| `--title`, `--artist`, `--album`, `--genre`, `--year`, `--charter` | Metadata. Defaults come from the file's tags, then its file name. |
| `--album-art IMG` | Crops and resizes any image to a 512×512 `album.png`. |
| `-o DIR` | Where to create the song folder (default: here). Point it at your Clone Hero `Songs` folder to skip copying. |
| `--force` | Overwrite an existing song folder. |
| `--instrument guitar\|bass` | Chart lead guitar (default) or bass. |
| `--separate auto\|on\|off` | Split the song into instruments and chart only the guitar. `auto` (default) does it when Demucs is installed; `off` charts the full mix. |
| `--fill smart\|gaps\|off` | Where the guitar isn't playing: `smart` (default) hands the part to another instrument in gaps and where the guitar only holds a chord while something else carries the song; `gaps` only where the guitar is silent; `off` keeps it guitar only, so those passages are rests. |
| `--fill-from piano,other` | Instruments allowed to fill in: any of `piano`, `other` (synth, organ, harmonica, strings…), `bass`, `vocals`. Default `piano,other`. |
| `--split-audio` | Write the charted part to `guitar.ogg` and the rest to `song.ogg`, so the part drops out when you miss notes. |
| `--stems FOLDER` | Use stems you already have (`guitar.wav`, `bass.wav`, `piano.wav`, `vocals.wav`, `drums.wav`, `other.wav`…). |
| `--stem FILE` | Use a single isolated guitar (or bass) track. |
| `--gate-db 45` | How far below its loud parts an instrument can be and still count as playing (bleed from other instruments is recognised separately). Lower it if faint noise gets charted. |
| `--midi FILE` | Also save what each separated instrument played as a MIDI file (open it in MuseScore to read it as a score). |
| `--no-borrow` | Don't fill bars where the guitar is buried from clearer repeats of the same part. |
| `--device auto\|cpu\|cuda\|mps` | Where Demucs runs. `--no-cache` skips the stem cache. |
| `--difficulties expert,hard` | Only generate some difficulties. |
| `--density 1.3` | More (above 1) or fewer (below 1) notes. |
| `--triplets` | Allow triplet timing, for shuffles and swing. |
| `--no-chords`, `--no-sustains`, `--min-sustain 0.5` | Chord and sustain control. |
| `--star-power-every 8` | Bars between star power phrases (0 = none). |
| `--bpm 140`, `--bpm-hint 140` | Force a tempo, or steer detection (useful if it picks half or double time). |
| `--tempo-map auto\|constant\|variable` | One BPM (songs recorded to a click) or follow the band's drift. |
| `--first-downbeat 2.31`, `--time-signature 3`, `--lead-in 1.5` | Bar placement fixes and the silent lead-in. |

### Examples

```
# Straight into your Clone Hero songs folder (Windows)
python autochart.py "Song.mp3" --split-audio -o "%USERPROFILE%\Documents\Clone Hero\Songs"

# Guitar only, no fill-ins: rests stay rests
python autochart.py song.flac --fill off

# Let the bass and the vocal line fill in too
python autochart.py song.mp3 --fill-from piano,other,bass,vocals

# You already separated it with another tool
python autochart.py song.mp3 --stems "song stems/"
```

## How it decides what to chart

Every instrument the song was split into is transcribed on its own: note
timings, pitches, chords and how long each note rings. Then, bar by bar:

- **The guitar wins wherever it is really playing, even quietly.** Separated
  stems always carry faint traces of the other instruments, so each stem is
  compared with all the others, band by band: whatever in the guitar stem is
  15 dB or more below another stem at the same pitch could be bleed, and only
  the rest counts as guitar. Each attack is checked the same way, so a piano
  chord or horn stab leaking into the guitar stem isn't charted, while a
  quiet guitar under a loud band still is.
- **Where the guitar is buried**, much quieter against the band than usual,
  and the same music (beat-by-beat harmony, including the bars around it)
  plays elsewhere with the guitar clearly audible, the chart uses that
  clearer bar's pattern, as long as what was heard fits it. `--no-borrow`
  turns this off; the log lists the bars it filled.
- **Where the guitar is silent**, the busiest allowed instrument (more notes
  and more varied pitches score higher; keys and synths are preferred over
  bass and vocals) can take over.
- **In `smart` mode** it can also take over when the guitar is only holding a
  chord while another instrument plays something worth playing.
- **If separation finds no clear guitar at all** (the song has none, or its
  tone ended up in another track, which happens with synth leads and unusual
  guitar sounds), every instrument is transcribed and the chart follows
  whatever carries the song, starting with "other". The log and the studio
  say when this happens.
- **Switching costs something**, and it costs less where a new section starts,
  so the chart sticks with an instrument for whole passages instead of
  flickering between them. The whole song is planned at once (a Viterbi
  search), not bar by bar in isolation.

The studio shows this plan, and you can repaint it before building.

## How the chart is made

1. **Tempo map.** The tempo is fitted to the attacks of the whole song at once
   (folded at the half beat, so a beat tracker that slips onto the off-beat in
   songs with off-beat strums or skanks can't make the tempo wobble), and the
   kick drum says which half is the beat. Steady songs get one exact BPM;
   songs that drift get a BPM marker wherever the tempo moves. Beat 1 of each bar comes from kick/bass hits and
   chord changes. A silent lead-in is added so the chart starts on a downbeat.
2. **Notes.** Attacks are found in each instrument's stem (a SuperFlux onset
   envelope, compressed and judged against the playing around it so soft
   strums between loud ones and quiet passages still register), pinned to the
   exact moment, checked for bleed, then snapped to a 1/16 (or 1/12) grid.
3. **Pitches and chords** come from Basic Pitch, a small neural network
   trained on real instruments: the keys that start at each attack (not the
   ones still ringing from before), minus overtones. Repeated strums of one
   chord are grouped and given one bass note and one size, so the chart
   repeats one shape, the way a human charts a strummed chord. Power chords
   become two-fret chords, full strums three.
4. **Frets** follow the melody's contour like a human charter: up the neck when
   the pitch rises, the same fret for a repeated note, bigger jumps for bigger
   leaps. A Viterbi search plans ahead so a rising run starts low enough to keep
   rising. Chords sit on the root.
5. **Sustains** come from how long each note rings.
6. **Difficulties.** Hard, Medium and Easy are subsets of Expert that keep the
   strongest, most on-beat notes, with wider spacing, fewer frets
   (Medium: green to blue, Easy: green to yellow) and smaller chords.
7. **Song structure.** Bars are grouped by harmony and timbre into sections
   (Intro, Verse, Chorus, Bridge, Outro); star power phrases are placed every
   few bars and the song preview starts at the first chorus.

## Testing

`tests/` builds songs whose every note is known and scores the charts:

```
python tests/make_band_song.py band.wav          # guitar, bass, drums, keys, vocals + their stems
python autochart.py band.wav --stems band_stems --artist Band --title Test
python tests/check_guitar.py "Band - Test" band.wav

# from recorded instrument samples (FluidR3_GM folder of github.com/gleitz/midi-js-soundfonts)
python tests/make_sampled_song.py song.wav --samples FluidR3_GM
python autochart.py song.wav --stems song_stems_bleed --fill off --artist T --title Sampled
python tests/check_guitar.py "T - Sampled" song.wav --sections

python tests/make_test_song.py test.wav --bpm 128   # timing/tempo tests (--drift 9 for live feel)
python tests/check_chart.py "Test - Test" test.wav
```

The band song has a keys-only intro and a break with no guitar, and its keys
and vocals play in the guitar's register on other rhythms. Expert results
with perfect stems:

| | Guitar notes caught | Chart notes that are guitar | Notes where the guitar is silent | Chords right / wrong |
| --- | --- | --- | --- | --- |
| Full mix, no separation | 99% | 77% | 52 (mostly keys, plus bleed) | 3 / 26 |
| Separated, `--fill off` | 100% | 100% | 0 | 41 / 9 |
| Separated, `--fill smart` (default) | 100% | 85% (the rest are deliberate keys fills) | 32, every one on a keys note | 41 / 25 |

The sampled song (recorded instruments; the guitar 14 dB quieter in the
second verse and 10 dB in the second chorus) with stems that have the other
instruments leaking in, `--fill off`, compared with the previous engine:

| Section | Previous engine: caught | Now: caught | Now: repeated chords keep one shape |
| --- | --- | --- | --- |
| Verse (clean off-beat chops) | 39 / 40 | 40 / 40 | 25 / 32 |
| Chorus (overdriven 8th strums) | 59 / 64 | 60 / 64 | 23 / 24 (was 1 / 21) |
| Solo (single notes) | 60 / 60 | 60 / 60 | |
| Verse 2 (guitar buried) | 0 / 40 | 38 / 40 | 21 / 29 |
| Chorus 2 (guitar quieter) | 59 / 64 | 61 / 64 | 21 / 23 (was 0 / 20) |

Overall 97% of guitar notes caught with 96% of chart notes on guitar notes;
with perfect stems, 98% and 100%. Timing tests: constant tempos come out
exact (128.00 and 152.00 BPM, beats within ~2 ms), a 112→121 BPM drift is
followed within ~5 ms, and bar lines land on the downbeat in every test.
Generated folders pass [scan-chart](https://github.com/Geomitron/scan-chart)
(the parser Chorus Encore uses to match Clone Hero) as playable with no chart
issues.

Every Windows build also runs both songs through real Demucs separation and
posts the scores on the run page. Real separation is messier than these stems:
on the sampled song, 88% of guitar notes are caught and 75% of chart notes are
guitar, with most strays coming from the piano leaking into the guitar track
in the piano-only intro and break, and only half the buried second verse is
recovered. The synthesized band's plucked-string "guitar" isn't recognised as
a guitar at all and lands in "other", which the empty-guitar fallback then
charts (90% caught). The separated tracks are kept as a build artifact for
tuning.

## Limitations

- Separation quality sets the ceiling. Demucs's guitar stem is good on rock and
  pop and weaker on dense mixes or clean guitar buried under keys.
- Borrowing from repeats only fills bars where the guitar is buried and the
  band plays the same music elsewhere; a part that is buried every time it
  plays can't be recovered that way.
- Section names are educated guesses from repetition and loudness.
- No forced HOPOs/taps, open notes, solos or lyrics. Clone Hero's automatic
  HOPOs apply (fast notes on different frets become HOPOs).
- Songs with heavy rubato or free-time intros may need tempo touch-ups in
  [Moonscraper](https://github.com/FireFox2000000/Moonscraper-Chart-Editor/releases).
- Chart only songs you have the rights to use.

## Credits

- [Demucs](https://github.com/adefossez/demucs) (MIT) separates the instruments.
- [Basic Pitch](https://github.com/spotify/basic-pitch) by Spotify (Apache
  License 2.0) transcribes notes; its model, license and notice ship in
  `models/basic_pitch/`.
- The sampled test song uses the FluidR3 soundfont as rendered by
  [midi-js-soundfonts](https://github.com/gleitz/midi-js-soundfonts)
  (CC BY 3.0), downloaded only for testing.
