import { copyFile, mkdir, readFile, access } from 'node:fs/promises';

// Ship the graph library locally: MV3 disallows remotely hosted scripts.
await mkdir(new URL('../vendor/', import.meta.url), { recursive: true });
for (const [source, target] of [
  ['cytoscape/dist/cytoscape.min.js', 'cytoscape.min.js'],
  ['cytoscape/LICENSE', 'CYTOSCAPE-LICENSE.txt'],
]) {
  await copyFile(new URL(`../node_modules/${source}`, import.meta.url), new URL(`../vendor/${target}`, import.meta.url));
}
const manifest = JSON.parse(await readFile(new URL('../manifest.json', import.meta.url)));
for (const path of [manifest.background.service_worker, manifest.side_panel.default_path, 'vendor/cytoscape.min.js']) {
  await access(new URL(`../${path}`, import.meta.url));
}
console.log('Extension ready. Load the extension/ directory unpacked in Chrome.');
