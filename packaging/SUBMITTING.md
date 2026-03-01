# Submitting TRCC Linux to Distro Repositories

Email for all accounts: siembabdavid@gmail.com

---

## 1. AUR (Arch / CachyOS / Manjaro)

**Quickest win — most users asking for native packages are on Arch-based distros.**

### One-time setup
1. Create account: https://aur.archlinux.org/register (use siembabdavid@gmail.com)
2. Add your SSH public key to your AUR profile:
   ```bash
   cat ~/.ssh/id_ed25519.pub
   # Paste into: https://aur.archlinux.org/account/YourUsername/ → SSH Public Key
   ```
3. If you don't have an SSH key:
   ```bash
   ssh-keygen -t ed25519 -C "siembabdavid@gmail.com"
   ```

### Submit
```bash
# Clone empty AUR repo
git clone ssh://aur@aur.archlinux.org/trcc-linux.git /tmp/aur-trcc-linux
cd /tmp/aur-trcc-linux

# Copy files
cp ~/Desktop/projects/thermalright/trcc-linux/packaging/arch/PKGBUILD .
cp ~/Desktop/projects/thermalright/trcc-linux/packaging/arch/.SRCINFO .

# Push
git add PKGBUILD .SRCINFO
git commit -m "Initial upload: trcc-linux 6.3.3"
git push
```

**Users install with:** `yay -S trcc-linux` or `paru -S trcc-linux`

---

## 2. Fedora Copr

**Self-hosted RPM repo — users add it like a PPA.**

### One-time setup
1. Create Fedora account: https://accounts.fedoraproject.org (use siembabdavid@gmail.com)
2. Go to https://copr.fedorainfracloud.org
3. Click "New Project" → name: `trcc-linux`, chroots: `fedora-rawhide-x86_64`, `fedora-41-x86_64`, `fedora-42-x86_64`

### Submit
1. In your Copr project, click "Packages" → "New Package"
2. Package name: `trcc-linux`
3. Source type: "PyPI" or "SCM" (Git)
   - SCM URL: `https://github.com/Lexonight1/thermalright-trcc-linux`
   - Spec file: `packaging/rpm/trcc-linux.spec`
4. Click "Build"

**Users install with:**
```bash
sudo dnf copr enable YourUsername/trcc-linux
sudo dnf install trcc-linux
```

---

## 3. openSUSE OBS (Open Build Service)

### One-time setup
1. Create account: https://build.opensuse.org (use siembabdavid@gmail.com)
2. Create home project: `home:YourUsername:trcc-linux`
3. Add repositories: openSUSE Tumbleweed, openSUSE Leap 15.6

### Submit
1. Go to your project → "Create Package" → name: `trcc-linux`
2. Upload files:
   - `trcc-linux.spec` (from `packaging/rpm/trcc-linux.spec`)
   - Source tarball: download from GitHub releases
3. Or use `osc` CLI:
   ```bash
   osc checkout home:YourUsername:trcc-linux
   cd home:YourUsername:trcc-linux/trcc-linux
   cp ~/Desktop/projects/thermalright/trcc-linux/packaging/rpm/trcc-linux.spec .
   wget https://github.com/Lexonight1/thermalright-trcc-linux/archive/v6.3.3.tar.gz -O trcc-linux-6.3.3.tar.gz
   osc add trcc-linux.spec trcc-linux-6.3.3.tar.gz
   osc commit -m "Initial submit: trcc-linux 6.3.3"
   ```

**Users install with:**
```bash
sudo zypper addrepo https://download.opensuse.org/repositories/home:YourUsername:trcc-linux/openSUSE_Tumbleweed/ trcc-linux
sudo zypper install trcc-linux
```

---

## 4. Ubuntu PPA (Launchpad)

### One-time setup
1. Create Launchpad account: https://launchpad.net/+login (use siembabdavid@gmail.com)
2. Create PPA: https://launchpad.net/~/+activate-ppa → name: `trcc-linux`
3. Upload your GPG key to Launchpad:
   ```bash
   gpg --gen-key  # if you don't have one
   gpg --keyserver keyserver.ubuntu.com --send-keys YOUR_KEY_ID
   ```

### Submit
```bash
cd ~/Desktop/projects/thermalright/trcc-linux

# Build source package
dpkg-source -b .

# Sign and upload
debsign ../trcc-linux_6.3.3-1_source.changes
dput ppa:YourUsername/trcc-linux ../trcc-linux_6.3.3-1_source.changes
```

**Users install with:**
```bash
sudo add-apt-repository ppa:YourUsername/trcc-linux
sudo apt install trcc-linux
```

---

## 5. Gentoo GURU Overlay

**GURU is Gentoo's community overlay — like AUR for Gentoo.**

### One-time setup
1. Fork https://github.com/gentoo/guru on GitHub
2. Clone your fork

### Submit
```bash
git clone https://github.com/YourUsername/guru.git /tmp/guru
cd /tmp/guru

# Create package directory
mkdir -p app-misc/trcc-linux

# Copy ebuild
cp ~/Desktop/projects/thermalright/trcc-linux/packaging/gentoo/trcc-linux-6.3.3.ebuild \
   app-misc/trcc-linux/

# Create metadata
cat > app-misc/trcc-linux/metadata.xml << 'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE pkgmetadata SYSTEM "https://www.gentoo.org/dtd/metadata.dtd">
<pkgmetadata>
  <maintainer type="person">
    <email>siembabdavid@gmail.com</email>
    <name>David Siemba</name>
  </maintainer>
  <longdescription>
    Linux implementation of the Thermalright LCD Control Center.
    Controls LCD displays and LED segment displays on Thermalright
    CPU coolers and AIO liquid coolers.
  </longdescription>
  <upstream>
    <remote-id type="github">Lexonight1/thermalright-trcc-linux</remote-id>
    <bugs-to>https://github.com/Lexonight1/thermalright-trcc-linux/issues</bugs-to>
  </upstream>
</pkgmetadata>
XML

# Generate manifest
cd app-misc/trcc-linux
ebuild trcc-linux-6.3.3.ebuild manifest

# Commit and PR
git add .
git commit -m "app-misc/trcc-linux: new package, version 6.3.3"
git push origin main
# Then open PR on https://github.com/gentoo/guru
```

**Users install with:**
```bash
eselect repository enable guru
emerge --sync guru
emerge app-misc/trcc-linux
```

---

## Priority Order

1. **AUR** — fastest, biggest impact (CachyOS users)
2. **Copr** — your Fedora users (you use Fedora yourself)
3. **OBS** — openSUSE
4. **PPA** — Ubuntu/Debian
5. **GURU** — Gentoo (smallest user base)
