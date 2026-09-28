# Erasing without your own NVIDIA GPU

The default eraser needs an NVIDIA GPU. If you do not have one (a Mac, or AMD or Intel graphics), you have
these options:

| Option | What runs remotely | You need | Good for |
|---|---|---|---|
| Skip erasing (default) | nothing | nothing | when the old text can stay, or for trying things out |
| [`--gpu modal`](#option-a---gpu-modal) | only the erase step | a Modal account | occasional videos, no server to manage |
| [Rent a GPU machine](#option-b-rent-a-gpu-machine) | the whole pipeline | a vast.ai / RunPod / similar account, SSH | batches of videos |
| The `lama` eraser on this machine | nothing | the `lama` extra | no account and no cost, when you accept slower erasing that is prone to flicker and flattens patterned backgrounds ([erasers.md](erasers.md#lama--for-machines-without-an-nvidia-gpu)) |

**Costs are yours.** These services bill your own account at their current prices. EraseDub has no affiliation
with them, earns nothing from them, and does not use referral links. Check their pricing pages before you start,
and always stop what you rent when you are done.

## Option A: `--gpu modal`

[Modal](https://modal.com) runs Python functions on cloud GPUs and bills per second of use. With `--gpu modal`,
EraseDub keeps everything on your machine except the erase step: it uploads the video to a function running in
**your** Modal account, erases the text there, and downloads the clean video.

1. Create an account at [modal.com](https://modal.com) and check its current pricing and any free allowance.
2. Add the `modal` extra to your install from source and log in (this opens a browser and writes a token to
   `~/.modal.toml`):

   ```bash
   uv sync --extra full --extra modal      # in the clone; or: pip install -e ".[full,modal]"
   modal token new
   ```

   Alternatively set `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` in the environment (for CI or servers).
   EraseDub never stores or prints the token.
3. Check it is detected:

   ```bash
   erasedub doctor          # the "modal" gpu provider should be available
   ```

4. Run with the erase step on Modal:

   ```bash
   erasedub run video.mp4 --to vi --gpu modal
   ```

   or set it once in `erasedub.toml`:

   ```toml
   [erase]
   gpu = "modal"
   ```

   Using `prepare` and `render` separately? `prepare` detects text on the CPU anyway (unless erasing is off), so
   the regions are there for `render`. Passing `--gpu modal` to **both** keeps the plans consistent. On the command
   line, `--gpu modal` also means erasing is required: if the `modal` extra or your Modal token is missing, the
   command stops with exit code 3 instead of skipping erasing. `gpu = "modal"` in `erasedub.toml` alone keeps the
   default `auto` (a notice instead of an error):

   ```bash
   erasedub prepare video.mp4 --to vi --gpu modal
   erasedub render  video.mp4 --to vi --gpu modal
   ```

Privacy: the video is uploaded to your Modal account for the duration of the erase step. Do not use this option for
videos you are not allowed to send to a third-party cloud.

## Option B: rent a GPU machine

Renting a whole machine with an NVIDIA GPU by the hour and running EraseDub there works with any provider that gives
you SSH access to a Linux machine with an NVIDIA driver. Two popular marketplaces are [vast.ai](https://vast.ai) and
[RunPod](https://www.runpod.io). The steps are the same:

1. **Create an account** and add credit. Read the provider's pricing, storage and data policies.
2. **Add your SSH public key** in the account settings.
3. **Pick a machine:**
   - one NVIDIA GPU is enough;
   - a template/image with a recent NVIDIA driver, e.g. an official PyTorch or Ubuntu CUDA image — or the EraseDub
     Docker image `ghcr.io/sting11k/erasedub` ([install/docker.md](install/docker.md)). Many CUDA images are based
     on Ubuntu 22.04, whose Python 3.10 is too old; the uv route below takes care of that;
   - enough disk for models (several GB) plus your videos;
   - prefer hosts marked as reliable / secure if the provider shows that.
4. **Start it and connect** with the SSH command the provider shows.
5. **Install EraseDub** as on any Linux machine ([install/linux.md](install/linux.md)). In rented containers you
   are usually `root` and `sudo` does not exist, so the commands have no `sudo`:

   ```bash
   apt-get update && apt-get install -y ffmpeg git pipx tmux
   pipx install uv && export PATH="$HOME/.local/bin:$PATH"
   git clone https://github.com/sting11k/erasedub ~/erasedub
   cd ~/erasedub && uv sync --python 3.12 --extra full
   source .venv/bin/activate
   erasedub doctor          # should show the GPU
   ```

6. **Upload your videos** from your computer (the host and port come from the provider's SSH command):

   ```bash
   scp -P <port> video.mp4 root@<host>:~/
   ```

7. **Run:**

   ```bash
   cd ~ && erasedub run video.mp4 --to vi
   ```

   For long batches, run inside `tmux` so a dropped connection does not stop the job.
8. **Download the results:**

   ```bash
   scp -P <port> "root@<host>:~/video.vi.mp4" .
   ```

9. **Destroy the instance** (not just stop it) in the provider's console when you are done. Stopped instances can
   keep charging for storage, and your videos stay on that disk until it is deleted.

Keys: if you use a paid translator or voice, set its API key as an environment variable on the rented machine for that
session only (see [configuration.md](configuration.md#api-keys-and-tokens)). Do not put keys in files there, because
marketplace hosts are other people's hardware.
