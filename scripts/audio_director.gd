extends Node
## Autoload `Audio`. Single owner of every sound the game makes.
##
## Centralised so that mixing is a property of the game rather than of whichever
## script happened to call play(). Brownout ducks the music, because the core
## mechanic has to be audible before it is read off a gauge (decision 003).

const SFX_DIR := "res://assets/audio/sfx/"
const MUSIC_DIR := "res://assets/audio/music/"
const VOICES := 12

var _sfx: Dictionary = {}
var _pool: Array[AudioStreamPlayer] = []
var _next: int = 0
var _music: AudioStreamPlayer
var _stinger: AudioStreamPlayer
var _music_db: float = 0.0
## Player-set gains, in dB, derived from the 0..1 sliders in the save.
var _music_gain_db: float = 0.0
var _sfx_gain_db: float = 0.0
var missing: Array[String] = []


func _ready() -> void:
	for i in range(VOICES):
		var p := AudioStreamPlayer.new()
		p.bus = "Master"
		add_child(p)
		_pool.append(p)
	_music = AudioStreamPlayer.new()
	_music.bus = "Master"
	add_child(_music)
	_stinger = AudioStreamPlayer.new()
	_stinger.bus = "Master"
	add_child(_stinger)
	# Sound keeps playing while the tree is paused: the pause overlay's volume sliders
	# are useless if nothing can be heard while they are being dragged.
	process_mode = Node.PROCESS_MODE_ALWAYS
	apply_volumes()


func apply_volumes() -> void:
	## Player volume, applied as a gain on top of whatever each cue asked for. Stored
	## as 0..1 in the save and converted here — linear_to_db is the only correct way to
	## turn a slider into a level, and doing it in the UI would spread that knowledge.
	_music_gain_db = linear_to_db(clampf(Progress.music_volume, 0.0, 1.0))
	_sfx_gain_db = linear_to_db(clampf(Progress.sfx_volume, 0.0, 1.0))
	_music.volume_db = _music_db + _music_gain_db


func _load(path: String) -> AudioStream:
	if not ResourceLoader.exists(path):
		if not missing.has(path):
			missing.append(path)
		return null
	return load(path)


func sfx(name: String, volume_db: float = 0.0) -> void:
	if not _sfx.has(name):
		_sfx[name] = _load(SFX_DIR + name + ".wav")
	var stream: AudioStream = _sfx[name]
	if stream == null:
		return
	var p := _pool[_next]
	_next = (_next + 1) % VOICES
	p.stream = stream
	p.volume_db = volume_db + _sfx_gain_db
	p.play()


func music(track_file: String, volume_db: float = -6.0) -> void:
	var stream := _load(MUSIC_DIR + track_file)
	if stream == null:
		return
	if stream is AudioStreamOggVorbis:
		stream.loop = true       # loops are baked into the file (decision 011)
	_music_db = volume_db
	_music.stream = stream
	_music.volume_db = volume_db + _music_gain_db
	_music.play()


func stinger(id: String) -> void:
	var names := {"SYS-WIN": "SYS-WIN_anchor_held.ogg", "SYS-LOS": "SYS-LOS_anchor_lost.ogg"}
	var stream := _load(MUSIC_DIR + String(names.get(id, "")))
	if stream == null:
		return
	_music.volume_db = _music_db + _music_gain_db - 12.0
	_stinger.stream = stream
	_stinger.volume_db = _music_gain_db
	_stinger.play()


func set_brownout(active: bool) -> void:
	## Duck the music while the bus is over. The player should hear the fault
	## before they read the gauge.
	_music.volume_db = _music_db + _music_gain_db + (-8.0 if active else 0.0)


func report() -> String:
	return "missing audio: %s" % ", ".join(missing) if missing.size() > 0 else "audio ok"
