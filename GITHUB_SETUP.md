# Publishing pronsoled to GitHub

## Steps to Publish

### 1. Create a GitHub Repository

Go to https://github.com/new and:
- **Repository name**: `pronsoled`
- **Description**: "Stable daemon wrapper for pronsole that solves multi-instance serial contention"
- **Public** (so others can find and use it)
- **Do NOT initialize with README** (we already have one)

### 2. Push to GitHub

From the pronsoled-repo directory:

```bash
cd pronsoled-repo

# Configure git (if not already done)
git config user.name "Your Name"
git config user.email "your.email@example.com"

# Add all files
git add .

# Initial commit
git commit -m "Initial commit: pronsoled daemon wrapper for pronsole"

# Add GitHub remote (replace YOUR_USERNAME)
git remote add origin https://github.com/YOUR_USERNAME/pronsoled.git

# Push to GitHub
git branch -M main
git push -u origin main
```

### 3. Update Files with Your Username

Before pushing, update these files to replace `YOUR_USERNAME`:

**README.md** (line 45):
```bash
git clone https://github.com/YOUR_USERNAME/pronsoled.git
```

**pronsoled.service** (line 2):
```
Documentation=https://github.com/YOUR_USERNAME/pronsoled
```

### 4. (Optional) Add Topics to GitHub

After creating the repo on GitHub, add these topics (helps discoverability):
- `3d-printing`
- `pronsole`
- `printrun`
- `daemon`
- `ender-3`
- `serial-communication`

### 5. (Optional) Add GitHub Actions for Testing

You can add a CI workflow to run tests. Create `.github/workflows/test.yml`:

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Install dependencies
        run: sudo apt-get install -y printrun
      - name: Run tests
        run: bash tests/test_pronsoled.sh
```

## Next Steps

1. Customize the files above with your GitHub username
2. Create the repo on GitHub
3. Push the code
4. Add a GitHub release for v1.0.0:
   - Tag: `v1.0.0`
   - Title: "Initial release"
   - Description: Notes about the stable daemon wrapper

## URL After Publishing

Your pronsoled repo will be at:
```
https://github.com/YOUR_USERNAME/pronsoled
```

Users can install with:
```bash
git clone https://github.com/YOUR_USERNAME/pronsoled.git
cd pronsoled
sudo bash install.sh
```
