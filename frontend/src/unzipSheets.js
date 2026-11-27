// Answer sheets chosen as zip files are opened here, so each student's sheet shows up in the
// list with its own roll number before anything is sent. Same rules as the server: every PDF,
// JPG or PNG inside is one sheet, named by its file name; Mac extras and other files are left out.

const SHEET = /\.(pdf|jpe?g|png)$/i;
const TYPES = { pdf: 'application/pdf', jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png' };

const isZip = (file) => /\.zip$/i.test(file.name);

/** The chosen files with every zip replaced by the sheets inside it. */
export async function expandZips(files) {
  if (!files.some(isZip)) return { files, skipped: [] };
  const { default: JSZip } = await import('jszip');
  const out = [];
  const skipped = [];
  for (const file of files) {
    if (!isZip(file)) {
      out.push(file);
      continue;
    }
    let archive;
    try {
      archive = await JSZip.loadAsync(file);
    } catch {
      throw new Error(`"${file.name}" is not a working zip file. Make the zip again and choose it.`);
    }
    const entries = Object.values(archive.files).filter((entry) => {
      if (entry.dir) return false;
      const parts = entry.name.split('/');
      if (parts.some((part) => part.startsWith('.') || part === '__MACOSX')) return false;
      if (!SHEET.test(entry.name)) {
        skipped.push(entry.name);
        return false;
      }
      return true;
    });
    // Two sheets with one name in different folders keep their folder name, so neither is lost.
    const stem = (path) => path.split('/').pop().replace(/\.[^.]+$/, '').toLowerCase();
    const counts = {};
    entries.forEach((entry) => { counts[stem(entry.name)] = (counts[stem(entry.name)] || 0) + 1; });
    const inside = await Promise.all(entries.map(async (entry) => {
      const parts = entry.name.split('/');
      const name = counts[stem(entry.name)] > 1 && parts.length > 1 ? parts.slice(-2).join('_') : parts.at(-1);
      const type = TYPES[name.split('.').pop().toLowerCase()];
      return new File([await entry.async('blob')], name, { type });
    }));
    if (inside.length === 0) throw new Error(`"${file.name}" has no PDF, JPG or PNG answer sheets inside.`);
    out.push(...inside.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })));
  }
  return { files: out, skipped };
}
