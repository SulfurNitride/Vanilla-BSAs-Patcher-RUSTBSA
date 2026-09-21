First release of the Rust BSA backend fork, based on Ungeziefi's Vanilla BSAs Patcher.

- Native Linux and Windows x86_64 applications, with the UI and patching assets included.
- Automatically downloads and verifies the latest stable Rust BSA/BA2 Handler CLI (0.0.4 or newer). A verified cached download is available when offline.
- Processes multiple BSAs at once: defaults to 2, configurable from 1 to 4. Each job has separate temporary files and shares the CPU budget.
- Opens the browser only after the local server responds, fixing the initial page-load failure.
- Preserves decompression, audio conversion, loose MP3 output, Vorbis upgrades, archive checks, and backups. Rebuilt archives are verified before replacement.
- Detects Linux Steam installations, including Flatpak and additional libraries, and handles differently capitalized filenames.

Download and extract the archive for your platform, then run **Vanilla BSAs Patcher** (`.exe` on Windows). Linux users may need `chmod +x "Vanilla BSAs Patcher"`. Linux builds target Ubuntu 24.04 or newer/equivalent (glibc 2.39+). No Python or Rust installation is needed; the first processing run needs internet access to download the archive CLI.

Both platforms are tested and built by GitHub Actions, including real BSA fixtures and a check that the packaged application's first browser request succeeds. No in-game compatibility testing is implied.
