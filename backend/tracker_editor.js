const byId = id => document.getElementById(id);
const rows = [...document.querySelectorAll('tbody tr')];
let directory, editing;

function parseStatus(text) {
  const match = text.replace(/^\uFEFF/, '').replace(/\r\n/g, '\n').match(/^Status: ([^\n]+)\nPosted: ([^\n]+)\nPosted source: ([^\n]+)\nNotes:\n([\s\S]*)$/);
  if (!match) throw Error('STATUS.txt has invalid labels. Fix the file before editing here.');
  return {status: match[1], posted: match[2], source: match[3], notes: match[4]};
}

function updatedStatus(original, status, posted, source, previousStatus, notes) {
  const old = parseStatus(original);
  for (const previous of [old.status, previousStatus]) {
    if ((afterApply.includes(previous) && !afterApply.includes(status)) ||
        (previous === 'SUBMISSION_UNKNOWN' && !afterApply.includes(status) && status !== previous)) {
      throw Error('An applied or uncertain application cannot be reset to an unapplied status.');
    }
  }
  if (posted !== 'UNKNOWN') {
    const match = posted.match(/^(\d{4}-\d{2}-\d{2})(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2}))?$/);
    const day = match && new Date(match[1] + 'T00:00:00Z');
    if (!match || !Number.isFinite(day.getTime()) || day.toISOString().slice(0, 10) !== match[1] || !Number.isFinite(Date.parse(posted))) {
      throw Error('Use a valid YYYY-MM-DD date, or ISO timestamp with timezone, or UNKNOWN.');
    }
    if (!source || source === 'UNKNOWN') throw Error('Supply the source/evidence for the posting date.');
  }
  if (!source || source.length > 2000 || /[\r\n]/.test(source)) throw Error('Supply a single-line source, or UNKNOWN.');
  if (notes === undefined) notes = old.notes;
  if (notes.length > 20000) throw Error('Keep notes within 20,000 characters.');
  return `Status: ${status}\nPosted: ${posted}\nPosted source: ${source}\nNotes:\n${notes}`;
}

async function saveStatus(handle, original, updated) {
  if (await (await handle.getFile()).text() !== original) {
    throw Error('This file changed since you opened it. Cancel and reopen the editor to load the latest values.');
  }
  const writer = await handle.createWritable();
  try {
    await writer.write(updated);
    await writer.close();
  } catch (error) {
    await writer.abort().catch(() => {});
    throw error;
  }
}

byId('q').addEventListener('input', e => {
  const query = e.target.value.toLowerCase();
  for (const row of rows) row.hidden = !row.textContent.toLowerCase().includes(query);
});
byId('connect').addEventListener('click', async () => {
  try {
    if (!window.showDirectoryPicker) throw Error('Editing requires Chrome or Edge. You can still edit STATUS.txt manually.');
    const selected = await window.showDirectoryPicker({mode: 'readwrite'});
    await selected.getFileHandle('APPLICATIONS.html');
    directory = selected;
    byId('message').textContent = 'Folder connected. Use Edit status / posted on a job.';
  } catch (error) {
    byId('message').textContent = error.name === 'AbortError' ? 'Folder selection canceled.' : error.message;
  }
});
document.querySelector('tbody').addEventListener('click', async e => {
  const button = e.target.closest('button[data-folder]');
  if (!button) return;
  try {
    if (!directory) throw Error('Select your job apps folder first.');
    const folder = await directory.getDirectoryHandle(button.dataset.folder);
    const handle = await folder.getFileHandle('STATUS.txt');
    const original = await (await handle.getFile()).text();
    const values = parseStatus(original);
    byId('status').value = values.status;
    if (!byId('status').value) throw Error('Unrecognized status in STATUS.txt; correct the file first.');
    byId('posted').value = values.posted;
    byId('posted-source').value = values.source;
    byId('notes').value = values.notes;
    const row = button.closest('tr');
    editing = {handle, original, row};
    byId('edit-title').textContent = row.cells[0].textContent + ' — ' + row.cells[1].textContent;
    byId('edit-error').textContent = '';
    byId('editor').showModal();
  } catch (error) { byId('message').textContent = error.message; }
});
byId('cancel').addEventListener('click', () => byId('editor').close());
byId('editor').addEventListener('cancel', e => { if (byId('save').disabled) e.preventDefault(); });
byId('edit-form').addEventListener('submit', async e => {
  e.preventDefault();
  byId('save').disabled = true;
  byId('cancel').disabled = true;
  try {
    const status = byId('status').value;
    const posted = byId('posted').value.trim() || 'UNKNOWN';
    const source = byId('posted-source').value.trim() || 'UNKNOWN';
    const notes = byId('notes').value;
    const updated = updatedStatus(editing.original, status, posted, source, editing.row.cells[3].textContent, notes);
    await saveStatus(editing.handle, editing.original, updated);
    editing.row.cells[3].textContent = status;
    editing.row.cells[4].textContent = posted;
    editing.row.cells[8].textContent = notes;
    byId('editor').close();
    byId('message').textContent = 'Saved to STATUS.txt. Backend records and the regenerated overview update on the next sync/hourly check.';
  } catch (error) { byId('edit-error').textContent = error.message; }
  finally { byId('save').disabled = false; byId('cancel').disabled = false; }
});
