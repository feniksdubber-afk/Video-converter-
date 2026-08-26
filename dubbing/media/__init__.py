"""dubbing/media/ — Step 2: ingestion foundation. Isolated media-processing
modules for the dubbing pipeline. Imports only pure functions from
utils.ffmpeg_utils; never touches the bot temp-dir setting or its path helper,
the bot async ffmpeg runner, the bot task queue, or handlers/.
"""
