const DB_NAME = "xylocopa-voice";
const DB_VERSION = 1;
const STORE = "jobs";

let _dbP = null;

function openDB() {
  if (!_dbP) {
    _dbP = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "key" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => { _dbP = null; reject(req.error); };
    });
  }
  return _dbP;
}

export async function getVoiceJob(key) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const req = db.transaction(STORE, "readonly").objectStore(STORE).get(key);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  });
}

export async function saveVoiceJob(key, data) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put({ key, ...data, ts: Date.now() });
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

export async function deleteVoiceJob(key) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).delete(key);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}

// Atomic read-and-delete in a single readwrite transaction.
// Returns the job (with text/status) if it existed, null otherwise.
// IndexedDB serializes concurrent transactions on the same store, so when
// pipeline and recovery race to claim the same key, exactly one of them gets
// the job back (the other gets null). This makes IDB the single source of
// truth for "has this transcript been claimed for delivery yet" and prevents
// the same recording from being delivered twice via two parallel paths.
export async function claimVoiceJob(key) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    const store = tx.objectStore(STORE);
    let job = null;
    const getReq = store.get(key);
    getReq.onsuccess = () => {
      job = getReq.result || null;
      if (job) store.delete(key);
    };
    tx.oncomplete = () => resolve(job);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}
