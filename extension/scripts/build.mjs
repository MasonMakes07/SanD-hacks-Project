import { readFile, access } from 'node:fs/promises';

// Native ES modules and local CSS; no generated graph bundle is required.
const manifest = JSON.parse(await readFile(new URL('../manifest.json', import.meta.url)));
for (const path of [manifest.background.service_worker, manifest.side_panel.default_path, 'board.html', 'theme.css', 'boardpage.css', 'sidepanel.css', 'src/board.js', 'src/boardpage.js', 'src/detail.js', 'src/graph.js', 'src/panel.js', 'src/presentation.js', 'src/walkthrough.js', 'src/loading.js', 'src/report.js', 'src/artwork.js', 'assets/artwork/empty.svg', 'assets/artwork/error.svg', 'assets/artwork/no-evidence.svg']) {
  await access(new URL(`../${path}`, import.meta.url));
}
console.log('Extension ready. Load the extension/ directory unpacked in Chrome.');
