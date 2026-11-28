# Titan Memory for Pi

Install the package from Pi with `pi install npm:titan-pi-memory` after its release. To try a checkout, use `pi install /absolute/path/to/titan-pi-memory`. Start Pi or reload its extensions. The first launch needs Python 3.10 or newer and downloads Python dependencies into Titan’s private virtual environment.

1. Run `/titan-setup` inside Pi. Choose an extraction provider and model, then an embedding provider and model. Enter any required keys in the browser and complete the provider checks. Pi shows the local URL if the browser does not open. Use `/titan-key` to reopen these settings.
2. Run `/titan-save Remember that the project uses violet labels for archived releases.` Pi reports whether Titan stored a memory, captured only a trace, or queued an extraction retry.
3. Restart Pi and run `/titan-query What labels does the project use for archived releases?` Use `/titan-recent` to inspect recent memories or `/titan-status` to see which workspace is connected.

The package includes the engine and stores Pi data under `~/.titan/agents/pi`. Existing configuration is preserved. Titan uses local HTTP port 8002 by default. To run an independent workspace, give it both a separate `TITAN_HOME` and a free `TITAN_PI_PORT`. Each Pi session stops the server it started when the session closes.

The `/memory-sync` workflow can import earlier Claude Code or Codex sessions. See the [project documentation](https://github.com/kuwosaad/titan-pi-memory) for storage and provider details.
