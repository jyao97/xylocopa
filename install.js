#!/usr/bin/env node
/**
 * Xylocopa — cross-platform interactive installer (Linux + macOS)
 *
 * Usage:
 *   npx create-xylocopa           ← one-liner (clones repo + full setup)
 *   node install.js               ← run inside an existing clone
 *   curl … | bash → setup.sh      ← bash bootstrap → this script
 */

const { execSync, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');
const readline = require('readline');

// ── Styling ─────────────────────────────────────────────────────────

const B = '\x1b[1m';
const DIM = '\x1b[2m';
const GREEN = '\x1b[32m';
const YELLOW = '\x1b[33m';
const RED = '\x1b[31m';
const CYAN = '\x1b[36m';
const R = '\x1b[0m';

const info  = (m) => console.log(`  ${GREEN}+${R} ${m}`);
const warn  = (m) => console.log(`  ${YELLOW}!${R} ${m}`);
const fail  = (m) => { console.error(`\n  ${RED}x ${m}${R}\n`); process.exit(1); };
const step  = (n, m) => console.log(`\n  ${CYAN}[${n}]${R} ${B}${m}${R}`);

function run(cmd, opts = {}) {
  try {
    return execSync(cmd, {
      stdio: opts.quiet ? 'pipe' : 'inherit',
      encoding: 'utf8',
      timeout: opts.timeout || 300_000,
      ...opts,
    });
  } catch (e) {
    if (opts.allowFail) return null;
    fail(`Command failed: ${cmd}\n    ${e.message.split('\n')[0]}`);
  }
}

function which(cmd) {
  try { return execSync(`which ${cmd} 2>/dev/null`, { encoding: 'utf8' }).trim(); }
  catch { return null; }
}

function ask(question, defaultVal) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const hint = defaultVal ? ` ${DIM}(${defaultVal})${R}` : '';
  return new Promise(resolve => {
    rl.question(`  ${B}>${R} ${question}${hint}: `, answer => {
      rl.close();
      resolve(answer.trim() || defaultVal || '');
    });
  });
}

function confirm(question, defaultYes = true) {
  const hint = defaultYes ? 'Y/n' : 'y/N';
  return ask(`${question} [${hint}]`, defaultYes ? 'y' : 'n')
    .then(a => a.toLowerCase().startsWith('y'));
}

const PLATFORM = os.platform();

// ── Package manager detection + install hints ───────────────────────

// Per-dep package names across package managers. Python is the only one
// that varies meaningfully — tmux/openssl/git have consistent names.
const DEP_PKGS = {
  python3: { apt: 'python3 python3-pip python3-venv', dnf: 'python3 python3-pip', pacman: 'python python-pip', zypper: 'python3 python3-pip', apk: 'python3 py3-pip', brew: 'python@3.12' },
  tmux:    { apt: 'tmux', dnf: 'tmux', pacman: 'tmux', zypper: 'tmux', apk: 'tmux', brew: 'tmux' },
  openssl: { apt: 'openssl', dnf: 'openssl', pacman: 'openssl', zypper: 'openssl', apk: 'openssl', brew: 'openssl' },
  git:     { apt: 'git', dnf: 'git', pacman: 'git', zypper: 'git', apk: 'git', brew: 'git' },
};

const PKG_MGRS = [
  { key: 'brew',   install: 'brew install',                   platform: 'darwin' },
  { key: 'apt',    install: 'sudo apt-get install -y',        platform: 'linux'  },
  { key: 'dnf',    install: 'sudo dnf install -y',            platform: 'linux'  },
  { key: 'pacman', install: 'sudo pacman -S --noconfirm',     platform: 'linux'  },
  { key: 'zypper', install: 'sudo zypper install -y',         platform: 'linux'  },
  { key: 'apk',    install: 'sudo apk add',                   platform: 'linux'  },
];

function detectPkgMgr() {
  for (const m of PKG_MGRS) {
    if (m.platform !== PLATFORM) continue;
    if (which(m.key)) return m;
  }
  return null;
}

// Print install instructions for the given missing deps and exit.
function failWithInstallHint(missingCmds) {
  console.log(`\n  ${YELLOW}Missing:${R} ${missingCmds.join(', ')}\n`);
  const mgr = detectPkgMgr();
  if (mgr) {
    const pkgs = missingCmds.map(c => DEP_PKGS[c][mgr.key]).filter(Boolean).join(' ');
    console.log(`  To install, run:\n`);
    console.log(`    ${B}${mgr.install} ${pkgs}${R}\n`);
    console.log(`  Then re-run this installer.\n`);
  } else if (PLATFORM === 'darwin') {
    console.log(`  Homebrew is not installed. Install it first:\n`);
    console.log(`    ${B}/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"${R}\n`);
    const pkgs = missingCmds.map(c => DEP_PKGS[c].brew).filter(Boolean).join(' ');
    console.log(`  Then install the missing packages:\n`);
    console.log(`    ${B}brew install ${pkgs}${R}\n`);
    console.log(`  Then re-run this installer.\n`);
  } else {
    console.log(`  Could not detect your package manager (checked: apt, dnf, pacman, zypper, apk).`);
    console.log(`  Please install the missing commands manually, then re-run.\n`);
  }
  process.exit(1);
}

// ── Main ────────────────────────────────────────────────────────────

async function main() {
  console.log(`
  ${B}==========================================${R}
  ${B}      Xylocopa Interactive Installer${R}
  ${B}==========================================${R}
  Platform: ${PLATFORM === 'darwin' ? 'macOS' : 'Linux'}
  Node:     ${process.version}
  `);

  // ─────────────────────────────────────────────────────────────────
  // Step 0: Determine install directory (npx vs local)
  // ─────────────────────────────────────────────────────────────────
  const isInsideRepo = fs.existsSync(path.join(process.cwd(), 'orchestrator', 'main.py'));
  let ROOT;

  if (isInsideRepo) {
    ROOT = process.cwd();
    info(`Running inside existing repo: ${ROOT}`);
  } else {
    step(0, 'Choose install location');
    const defaultDir = path.join(os.homedir(), 'xylocopa');
    const installDir = await ask('Install directory', defaultDir);
    ROOT = path.resolve(installDir);

    if (fs.existsSync(path.join(ROOT, 'orchestrator', 'main.py'))) {
      info(`Xylocopa already cloned at ${ROOT}`);
    } else {
      if (!which('git')) {
        warn('git is required to clone the repository but was not found');
        failWithInstallHint(['git']);
      }
      info(`Cloning Xylocopa to ${ROOT}...`);
      run(`git clone https://github.com/jyao97/xylocopa.git "${ROOT}"`);
      info('Repository cloned');
    }
  }

  // ─────────────────────────────────────────────────────────────────
  // Step 1: System dependencies
  // ─────────────────────────────────────────────────────────────────
  step(1, 'Checking system dependencies');

  const depCmds = ['python3', 'tmux', 'openssl', 'git'];
  const missing = depCmds.filter(cmd => !which(cmd));

  if (missing.length) {
    failWithInstallHint(missing);
  } else {
    info('python3, tmux, openssl, git — all found');
  }

  // ─────────────────────────────────────────────────────────────────
  // Step 2: Claude Code CLI
  // ─────────────────────────────────────────────────────────────────
  step(2, 'Claude Code CLI');

  if (which('claude')) {
    const ver = run('claude --version 2>/dev/null || echo unknown', { quiet: true });
    info(`Claude CLI found: ${(ver || '').trim()}`);
  } else {
    warn('Claude Code CLI not found');
    if (await confirm('Install @anthropic-ai/claude-code globally?')) {
      run('npm install -g @anthropic-ai/claude-code');
      info('Claude Code CLI installed');
    } else {
      warn('Skipped — you can install later: npm install -g @anthropic-ai/claude-code');
    }
  }

  // ─────────────────────────────────────────────────────────────────
  // Step 3: pm2 (process manager)
  // ─────────────────────────────────────────────────────────────────
  step(3, 'Process manager (pm2)');

  if (which('pm2')) {
    info('pm2 found');
  } else {
    info('Installing pm2...');
    run('npm install -g pm2');
    info('pm2 installed');
  }

  // ─────────────────────────────────────────────────────────────────
  // Step 4: Python virtual environment + dependencies
  // ─────────────────────────────────────────────────────────────────
  step(4, 'Python environment');

  const venvDir = path.join(ROOT, '.venv');
  if (!fs.existsSync(venvDir)) {
    info('Creating virtual environment...');
    run(`python3 -m venv "${venvDir}"`);
  } else {
    info('Virtual environment exists');
  }

  const pip = path.join(venvDir, 'bin', 'pip');
  info('Installing Python packages...');
  run(`"${pip}" install --upgrade pip -q`, { quiet: true });
  run(`"${pip}" install -q -r "${path.join(ROOT, 'orchestrator', 'requirements.txt')}"`);
  info('Python dependencies installed');

  // ─────────────────────────────────────────────────────────────────
  // Step 5: Frontend dependencies
  // ─────────────────────────────────────────────────────────────────
  step(5, 'Frontend dependencies');

  info('Running npm install...');
  run('npm install', { cwd: path.join(ROOT, 'frontend') });
  info('Frontend dependencies installed');

  // ─────────────────────────────────────────────────────────────────
  // Step 6: Configuration (.env)
  // ─────────────────────────────────────────────────────────────────
  step(6, 'Configuration');

  const envFile = path.join(ROOT, '.env');
  const envExample = path.join(ROOT, '.env.example');

  if (fs.existsSync(envFile)) {
    info('.env already exists — skipping');
  } else {
    let envContent = fs.readFileSync(envExample, 'utf8');

    // Projects directory
    const defaultProjectsDir = path.join(os.homedir(), 'xylocopa-projects');
    const projectsDir = await ask('Where should project repos live?', defaultProjectsDir);
    envContent = envContent.replace(/^HOST_PROJECTS_DIR=.*$/m, `HOST_PROJECTS_DIR=${projectsDir}`);
    fs.mkdirSync(projectsDir, { recursive: true });
    info(`Projects directory: ${projectsDir}`);

    // OpenAI API key (optional, for voice input)
    console.log(`\n  ${DIM}Voice input uses OpenAI Whisper for speech-to-text.${R}`);
    const openaiKey = await ask('OpenAI API key (optional, press Enter to skip)', '');
    if (openaiKey) {
      envContent = envContent.replace(/^OPENAI_API_KEY=.*$/m, `OPENAI_API_KEY=${openaiKey}`);
      info('OpenAI API key set');
    } else {
      info('Voice input skipped — you can add OPENAI_API_KEY to .env later');
    }

    // Claude model
    console.log(`\n  ${DIM}Available models: claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5-20251001${R}`);
    const model = await ask('Default Claude model', 'claude-opus-4-7');
    envContent = envContent.replace(/^CC_MODEL=.*$/m, `CC_MODEL=${model}`);
    info(`Default model: ${model}`);

    // Ports
    const port = await ask('Backend API port', '8080');
    const fport = await ask('Frontend HTTPS port', '3000');
    envContent = envContent.replace(/^PORT=.*$/m, `PORT=${port}`);
    envContent = envContent.replace(/^FRONTEND_PORT=.*$/m, `FRONTEND_PORT=${fport}`);

    fs.writeFileSync(envFile, envContent);
    info('.env created');
  }

  // ─────────────────────────────────────────────────────────────────
  // Step 7: Required directories
  // ─────────────────────────────────────────────────────────────────
  for (const dir of ['data', 'logs', 'backups', 'project-configs']) {
    fs.mkdirSync(path.join(ROOT, dir), { recursive: true });
  }

  // ─────────────────────────────────────────────────────────────────
  // Step 8: SSL certificates
  // ─────────────────────────────────────────────────────────────────
  step(7, 'SSL certificates');

  const certsDir = path.join(ROOT, 'certs');
  if (fs.existsSync(path.join(certsDir, 'selfsigned.crt'))) {
    info('Certificates already exist');
  } else {
    // Detect LAN IP
    let lanIp = '127.0.0.1';
    if (PLATFORM === 'linux') {
      const r = spawnSync('hostname', ['-I'], { encoding: 'utf8' });
      if (r.status === 0) lanIp = r.stdout.trim().split(/\s+/)[0] || '127.0.0.1';
    } else {
      // macOS: try en0 first, then en1 (some Macs use en1 for Wi-Fi)
      for (const iface of ['en0', 'en1']) {
        const r = spawnSync('ipconfig', ['getifaddr', iface], { encoding: 'utf8' });
        if (r.status === 0 && r.stdout.trim()) { lanIp = r.stdout.trim(); break; }
      }
    }

    console.log(`\n  ${DIM}HTTPS is required for mobile microphone access and PWA install.${R}`);
    console.log(`  ${DIM}A self-signed certificate will be generated for LAN use.${R}`);
    info(`Detected LAN IP: ${B}${lanIp}${R}`);

    const certIp = await ask('LAN IP for certificate (verify above)', lanIp);
    fs.mkdirSync(certsDir, { recursive: true });

    run(
      `openssl req -x509 -nodes -days 365 -newkey rsa:2048 ` +
      `-keyout "${path.join(certsDir, 'selfsigned.key')}" ` +
      `-out "${path.join(certsDir, 'selfsigned.crt')}" ` +
      `-subj "/CN=xylocopa" ` +
      `-addext "subjectAltName=DNS:xylocopa,DNS:localhost,IP:127.0.0.1,IP:${certIp}"`,
      { quiet: true }
    );
    info('Certificate generated');

    // Trust certificate
    if (await confirm('Trust certificate system-wide? (requires sudo password)')) {
      if (PLATFORM === 'linux' && fs.existsSync('/usr/local/share/ca-certificates')) {
        run(`sudo cp "${path.join(certsDir, 'selfsigned.crt')}" /usr/local/share/ca-certificates/xylocopa.crt`, { allowFail: true });
        run('sudo update-ca-certificates 2>/dev/null', { allowFail: true, quiet: true });
        info('Certificate trusted (Linux)');
      } else if (PLATFORM === 'darwin') {
        run(
          `sudo security add-trusted-cert -d -r trustRoot ` +
          `-k /Library/Keychains/System.keychain ` +
          `"${path.join(certsDir, 'selfsigned.crt')}"`,
          { allowFail: true }
        );
        info('Certificate trusted (macOS)');
      }
    } else {
      warn('Skipped — browser will show a security warning on first visit');
      if (PLATFORM === 'darwin') {
        console.log(`  ${DIM}To trust later: sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain certs/selfsigned.crt${R}`);
      }
    }
  }

  // ─────────────────────────────────────────────────────────────────
  // Step 9: Project registry
  // ─────────────────────────────────────────────────────────────────
  const registryFile = path.join(ROOT, 'project-configs', 'registry.yaml');
  const registryExample = path.join(ROOT, 'project-configs', 'registry.yaml.example');
  if (!fs.existsSync(registryFile) && fs.existsSync(registryExample)) {
    fs.copyFileSync(registryExample, registryFile);
  }

  // ─────────────────────────────────────────────────────────────────
  // Step 10: Claude CLI auth check
  // ─────────────────────────────────────────────────────────────────
  step(8, 'Claude CLI authentication');

  const claudeHome = path.join(os.homedir(), '.claude');
  const credFile = path.join(claudeHome, '.credentials.json');

  if (fs.existsSync(credFile)) {
    info('Claude credentials found');
  } else {
    warn('No Claude credentials detected');
    console.log(`  ${DIM}Xylocopa needs an authenticated Claude CLI to run agents.${R}`);
    if (which('claude')) {
      if (await confirm('Run "claude" now to authenticate?')) {
        console.log(`\n  ${DIM}This will open a browser window for OAuth login.${R}`);
        console.log(`  ${DIM}Complete the login, then return here.${R}\n`);
        run('claude --version', { allowFail: true });  // triggers auth flow
        info('Check complete');
      } else {
        warn('Skipped — run "claude" manually before starting Xylocopa');
      }
    } else {
      warn('Install Claude CLI first, then run "claude" to authenticate');
    }
  }

  // ─────────────────────────────────────────────────────────────────
  // Step 11: Start?
  // ─────────────────────────────────────────────────────────────────
  // Read ports from .env
  let port = '8080', fport = '3000';
  try {
    const env = fs.readFileSync(path.join(ROOT, '.env'), 'utf8');
    const pm = env.match(/^PORT=(.+)$/m);
    const fm = env.match(/^FRONTEND_PORT=(.+)$/m);
    if (pm) port = pm[1].trim();
    if (fm) fport = fm[1].trim();
  } catch {}

  console.log(`
  ${B}==========================================${R}
  ${GREEN}${B}  Installation complete!${R}
  ${B}==========================================${R}

  ${B}Location:${R}  ${ROOT}
  ${B}Backend:${R}   http://localhost:${port}
  ${B}Frontend:${R}  https://localhost:${fport}
  `);

  step(9, 'Launch');
  const startNow = await confirm('Start Xylocopa now?');
  if (startNow) {
    process.chdir(ROOT);
    run(`bash "${path.join(ROOT, 'run.sh')}" start`);
  }

  // Auto-start on boot — asked independently of "start now" so users who
  // decline the immediate launch can still opt in.
  //
  // Why this matters beyond convenience: running pm2 startup moves the pm2
  // daemon into a systemd user service (Linux) or launchd agent (macOS),
  // lifting it out of whatever terminal scope it was spawned in. On Linux
  // that escapes the GNOME Terminal's vte-spawn cgroup, which systemd-oomd
  // can SIGKILL wholesale under memory pressure — taking backend+frontend
  // with it. pm2 save persists the current process list so boot-time
  // resurrect brings everything back.
  console.log();
  if (await confirm('Enable auto-start on boot? (pm2 startup)')) {
    if (startNow) {
      run('pm2 save', { allowFail: true });
    } else {
      warn('Skipping pm2 save — no running processes to persist yet.');
      warn('After first "./run.sh start", run "pm2 save" to record them.');
    }
    console.log();
    run('pm2 startup', { allowFail: true });
    console.log(`\n  ${DIM}If pm2 printed a sudo command above, copy and run it to complete setup.${R}`);
  }

  if (!startNow) {
    console.log(`
  To start later:
    ${B}cd ${ROOT}${R}
    ${B}./run.sh${R}
    `);
  }
}

main().catch(e => { fail(e.message); });
