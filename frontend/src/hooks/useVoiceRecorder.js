import { useState, useRef, useCallback, useEffect } from "react";
import { transcribeVoice, refineVoiceText } from "../lib/api";
import { getVoiceJob, saveVoiceJob, deleteVoiceJob } from "../lib/voiceStore";

export const DEFAULT_MAX_RECORDING_MS = 300000; // 5 minutes

export default function useVoiceRecorder({ onTranscript, onError, maxDurationMs, persistKey }) {
  const limit = maxDurationMs || DEFAULT_MAX_RECORDING_MS;
  const [recording, setRecording] = useState(false);
  const [voiceLoading, setVoiceLoading] = useState(false);
  const [refining, setRefining] = useState(false);
  const [micError, setMicError] = useState(null);
  const [analyserNode, setAnalyserNode] = useState(null);
  const [remainingSeconds, setRemainingSeconds] = useState(limit / 1000);

  const streamRef = useRef(null);
  const audioCtxRef = useRef(null);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const timerRef = useRef(null);
  const countdownRef = useRef(null);
  const startTimeRef = useRef(null);
  const startingRef = useRef(false);
  const wakeLockRef = useRef(null);
  // Snapshot of persistKey at recording-start time. Bound to this recording
  // instance so the pipeline routes the transcript back to the conversation
  // it was recorded in, even if the user navigates mid-pipeline.
  const recordingKeyRef = useRef(null);

  const mountedRef = useRef(true);
  useEffect(() => { return () => { mountedRef.current = false; }; }, []);

  const onTranscriptRef = useRef(onTranscript);
  const onErrorRef = useRef(onError);
  const limitRef = useRef(limit);
  const persistKeyRef = useRef(persistKey);
  useEffect(() => { onTranscriptRef.current = onTranscript; }, [onTranscript]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);
  useEffect(() => { limitRef.current = limit; }, [limit]);
  useEffect(() => { persistKeyRef.current = persistKey; }, [persistKey]);

  useEffect(() => {
    if (!recording) setRemainingSeconds(limit / 1000);
  }, [limit, recording]);

  // --------------- unified pipeline ---------------
  // Runs transcribe → refine → deliver. Accepts either an audio blob
  // (fresh recording or retry) or already-transcribed rawText (resume).
  // Persists intermediate results to IndexedDB so the pipeline can
  // survive page freeze / kill and resume on next mount.

  const runPipeline = useCallback(async (audioBlob, mimeType, rawText, capturedKey) => {
    // capturedKey is the persistKey that was active when this pipeline started
    // (recording-start or recovery). Use it so navigation mid-pipeline can't
    // misroute the transcript. Falls back to the current ref only when caller
    // didn't pass one (defensive — all internal call sites do).
    const key = capturedKey !== undefined ? capturedKey : persistKeyRef.current;

    // Step 1 — transcribe (skip if we already have rawText)
    if (!rawText) {
      if (!audioBlob || audioBlob.size < 100) {
        if (key) deleteVoiceJob(key).catch(() => {});
        return;
      }
      setVoiceLoading(true);
      try {
        const result = await transcribeVoice(audioBlob, mimeType);
        rawText = (result.text || "").trim();
        if (!rawText) {
          if (key) deleteVoiceJob(key).catch(() => {});
          return;
        }
        // Persist transcription — blob no longer needed
        if (key) saveVoiceJob(key, { status: "transcribed", rawText }).catch(() => {});
      } catch (err) {
        const msg = err?.message || "";
        if (msg.includes("503") || msg.toLowerCase().includes("api key")) {
          onErrorRef.current?.("Voice input unavailable — OpenAI API key not configured. Add OPENAI_API_KEY to .env");
        } else {
          onErrorRef.current?.("Transcription failed — try again.");
        }
        return;
      } finally {
        setVoiceLoading(false);
      }
    }

    // Step 2 — refine (optional)
    const shouldRefine = (() => {
      try { return localStorage.getItem("pref:voiceRefine") !== "false"; } catch { return true; }
    })();

    let finalText = rawText;
    if (shouldRefine && rawText.length >= 2) {
      setRefining(true);
      try {
        const res = await refineVoiceText(rawText);
        finalText = res.text;
      } catch {
        // refine failed — fall back to raw text
      } finally {
        setRefining(false);
      }
    }

    // Step 3 — persist final result, then deliver only if still on the
    // same conversation. If persistKey has changed (user navigated to a
    // different chat mid-pipeline), DO NOT call onTranscript — that would
    // dump this transcript into the wrong textarea. Leave the "done" entry
    // in IndexedDB so the recovery effect picks it up when the user returns.
    if (key) {
      await saveVoiceJob(key, { status: "done", text: finalText }).catch((e) => {
        console.warn("[voice] failed to save done entry:", e);
      });
    }
    const stillSameKey = !key || persistKeyRef.current === key;
    console.log("[voice] pipeline done, mounted:", mountedRef.current, "key:", key, "current:", persistKeyRef.current, "stillSameKey:", stillSameKey, "text:", finalText?.slice(0, 40));
    if (stillSameKey && mountedRef.current) {
      onTranscriptRef.current?.(finalText);
      if (key) {
        console.log("[voice] deleting entry (delivered)");
        deleteVoiceJob(key).catch(() => {});
      }
    } else if (!stillSameKey) {
      console.log("[voice] keeping entry (persistKey changed; user navigated)");
    } else {
      console.log("[voice] keeping entry (component unmounted)");
    }
  }, []);

  // --------------- recovery on mount ---------------

  useEffect(() => {
    if (!persistKey) return;
    let cancelled = false;
    const recoveryKey = persistKey;

    console.log("[voice] recovery check for key:", persistKey);
    getVoiceJob(persistKey).then((job) => {
      if (cancelled) { console.log("[voice] recovery cancelled (unmounted)"); return; }
      if (!job) { console.log("[voice] no pending job found"); return; }
      console.log("[voice] recovery found job:", job.status, "text:", (job.text || job.rawText || "")?.slice(0, 40));

      // Guard: another navigation may have happened between the IndexedDB
      // read and now. Only deliver if we're still on the recovered chat.
      if (persistKeyRef.current !== recoveryKey) {
        console.log("[voice] recovery aborted — persistKey changed:", persistKeyRef.current);
        return;
      }

      if (job.status === "done") {
        onTranscriptRef.current?.(job.text);
        deleteVoiceJob(recoveryKey).catch(() => {});
      } else if (job.status === "transcribed") {
        runPipeline(null, null, job.rawText, recoveryKey);
      } else if (job.status === "pending" && job.audioBlob) {
        runPipeline(job.audioBlob, job.mimeType, null, recoveryKey);
      }
    }).catch((e) => { console.warn("[voice] recovery error:", e); });

    return () => { cancelled = true; };
  }, [persistKey, runPipeline]);

  // --------------- wake lock ---------------

  const acquireWakeLock = useCallback(async () => {
    if (!navigator.wakeLock) return;
    try {
      wakeLockRef.current = await navigator.wakeLock.request("screen");
      wakeLockRef.current.addEventListener("release", () => { wakeLockRef.current = null; });
    } catch {}
  }, []);

  const releaseWakeLock = useCallback(() => {
    if (wakeLockRef.current) {
      wakeLockRef.current.release().catch(() => {});
      wakeLockRef.current = null;
    }
  }, []);

  // --------------- cleanup ---------------

  const cleanup = useCallback(() => {
    releaseWakeLock();
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    if (countdownRef.current) { clearInterval(countdownRef.current); countdownRef.current = null; }
    if (recorderRef.current) {
      try { if (recorderRef.current.state !== "inactive") recorderRef.current.stop(); } catch {}
      recorderRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    setAnalyserNode(null);
  }, [releaseWakeLock]);

  useEffect(() => {
    return () => { cleanup(); };
  }, [cleanup]);

  // --------------- recording ---------------

  const startRecording = useCallback(async () => {
    if (startingRef.current || voiceLoading) return;
    startingRef.current = true;
    setMicError(null);
    chunksRef.current = [];

    // Snapshot persistKey for this recording instance. Used by recorder.onstop
    // and the pipeline so navigation mid-recording can't reroute the result.
    recordingKeyRef.current = persistKeyRef.current;

    // Clear any stale pending job for this key
    if (recordingKeyRef.current) deleteVoiceJob(recordingKeyRef.current).catch(() => {});

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      startingRef.current = false;
      if (window.location.protocol === "http:" && window.location.hostname !== "localhost") {
        setMicError("Microphone requires HTTPS. Open this page via https:// or localhost.");
      } else {
        setMicError("cert_needed");
      }
      return;
    }

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      startingRef.current = false;
      if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
        setMicError("cert_needed");
      } else if (err.name === "NotFoundError" || err.name === "NotReadableError") {
        setMicError("No microphone detected — plug one in or check your system audio settings.");
      } else if (err.name === "AbortError") {
        setMicError("Microphone access was interrupted — try again.");
      } else {
        setMicError("cert_needed");
      }
      return;
    }

    try {
      streamRef.current = stream;

      const audioCtx = new AudioContext();
      audioCtxRef.current = audioCtx;
      if (audioCtx.state === "suspended") await audioCtx.resume();
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      setAnalyserNode(analyser);

      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/mp4")
          ? "audio/mp4"
          : "";

      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = () => {
        const chunks = chunksRef.current;
        chunksRef.current = [];
        if (chunks.length === 0) return;
        const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        const mime = recorder.mimeType || "audio/webm";
        // Use the snapshot from startRecording, not the live ref — the user
        // may have navigated between recorder.stop() and onstop firing.
        const key = recordingKeyRef.current;

        if (key) {
          saveVoiceJob(key, { status: "pending", audioBlob: blob, mimeType: mime })
            .then(() => runPipeline(blob, mime, null, key))
            .catch(() => runPipeline(blob, mime, null, key));
        } else {
          runPipeline(blob, mime, null, key);
        }
      };

      recorder.start(1000);
      acquireWakeLock();

      const curLimit = limitRef.current;
      setRecording(true);
      startTimeRef.current = Date.now();
      setRemainingSeconds(curLimit / 1000);

      countdownRef.current = setInterval(() => {
        const elapsed = Date.now() - startTimeRef.current;
        const remaining = Math.max(0, Math.ceil((limitRef.current - elapsed) / 1000));
        setRemainingSeconds(remaining);
      }, 1000);

      timerRef.current = setTimeout(() => {
        stopRecordingInternal();
      }, curLimit);

    } catch (err) {
      stream.getTracks().forEach((t) => t.stop());
      if (audioCtxRef.current) audioCtxRef.current.close().catch(() => {});
      setMicError("Could not start recording — try again.");
    } finally {
      startingRef.current = false;
    }
  }, [voiceLoading, cleanup, runPipeline, acquireWakeLock]);

  const stopRecordingInternal = useCallback(() => {
    releaseWakeLock();
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null; }
    if (countdownRef.current) { clearInterval(countdownRef.current); countdownRef.current = null; }

    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      try { recorder.stop(); } catch {}
    }
    recorderRef.current = null;

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    setAnalyserNode(null);

    setRecording(false);
    startTimeRef.current = null;
    setRemainingSeconds(limitRef.current / 1000);
  }, [releaseWakeLock]);

  const stopRecording = useCallback(() => {
    stopRecordingInternal();
  }, [stopRecordingInternal]);

  const toggleRecording = useCallback(() => {
    if (recording) stopRecording();
    else startRecording();
  }, [recording, startRecording, stopRecording]);

  return {
    recording,
    voiceLoading,
    refining,
    micError,
    analyserNode,
    remainingSeconds,
    streamingText: "",
    startRecording,
    stopRecording,
    toggleRecording,
  };
}
