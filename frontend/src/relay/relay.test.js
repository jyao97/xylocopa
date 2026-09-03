import { describe, it, expect, beforeEach } from "vitest";
import {
  STORAGE_KEY,
  parseParams,
  isAllowedReturn,
  isDropboxAuthorizeUrl,
  decide,
} from "../../../docs/oauth/dropbox/relay.js";

describe("relay.js", () => {
  // --- parseParams ---
  describe("parseParams", () => {
    it("parses hash-style params", () => {
      expect(parseParams("#a=1&b=2")).toEqual({ a: "1", b: "2" });
    });

    it("parses search-style params", () => {
      expect(parseParams("?code=abc&state=xyz")).toEqual({ code: "abc", state: "xyz" });
    });

    it("parses bare params (no leading # or ?)", () => {
      expect(parseParams("foo=bar")).toEqual({ foo: "bar" });
    });

    it("returns empty object for empty/falsy input", () => {
      expect(parseParams("")).toEqual({});
      expect(parseParams(null)).toEqual({});
      expect(parseParams(undefined)).toEqual({});
    });

    it("handles keys with no value", () => {
      expect(parseParams("#key")).toEqual({ key: "" });
    });

    it("decodes percent-encoded values", () => {
      expect(parseParams("#return=https%3A%2F%2Flocalhost%3A3000")).toEqual({
        return: "https://localhost:3000",
      });
    });
  });

  // --- isAllowedReturn ---
  describe("isAllowedReturn", () => {
    it("allows https origins", () => {
      expect(isAllowedReturn("https://example.com")).toBe(true);
      expect(isAllowedReturn("https://myhost.example.com:8443")).toBe(true);
    });

    it("allows http localhost variants", () => {
      expect(isAllowedReturn("http://localhost")).toBe(true);
      expect(isAllowedReturn("http://localhost:3000")).toBe(true);
      expect(isAllowedReturn("http://127.0.0.1")).toBe(true);
      expect(isAllowedReturn("http://127.0.0.1:8080")).toBe(true);
      expect(isAllowedReturn("http://[::1]")).toBe(true);
      expect(isAllowedReturn("http://[::1]:3000")).toBe(true);
    });

    it("allows http *.local", () => {
      expect(isAllowedReturn("http://mybox.local")).toBe(true);
      expect(isAllowedReturn("http://mybox.local:3000")).toBe(true);
    });

    it("allows http private-range IPs", () => {
      // 10.x
      expect(isAllowedReturn("http://10.0.0.1")).toBe(true);
      expect(isAllowedReturn("http://10.255.255.255:3000")).toBe(true);
      // 172.16-31.x
      expect(isAllowedReturn("http://172.16.0.1")).toBe(true);
      expect(isAllowedReturn("http://172.31.255.255:3000")).toBe(true);
      // 192.168.x
      expect(isAllowedReturn("http://192.168.0.1")).toBe(true);
      expect(isAllowedReturn("http://192.168.1.100:3000")).toBe(true);
    });

    it("rejects http public addresses", () => {
      expect(isAllowedReturn("http://example.com")).toBe(false);
      expect(isAllowedReturn("http://172.32.0.1")).toBe(false);
      expect(isAllowedReturn("http://172.15.0.1")).toBe(false);
      expect(isAllowedReturn("http://11.0.0.1")).toBe(false);
    });

    it("rejects origins with paths", () => {
      expect(isAllowedReturn("https://example.com/foo")).toBe(false);
    });

    it("rejects non-http(s) protocols", () => {
      expect(isAllowedReturn("ftp://example.com")).toBe(false);
      expect(isAllowedReturn("file:///tmp")).toBe(false);
    });

    it("rejects falsy/invalid input", () => {
      expect(isAllowedReturn("")).toBe(false);
      expect(isAllowedReturn(null)).toBe(false);
      expect(isAllowedReturn("not-a-url")).toBe(false);
    });
  });

  // --- isDropboxAuthorizeUrl ---
  describe("isDropboxAuthorizeUrl", () => {
    it("accepts valid Dropbox authorize URLs", () => {
      expect(isDropboxAuthorizeUrl("https://www.dropbox.com/oauth2/authorize?client_id=abc")).toBe(true);
    });

    it("rejects URLs with wrong prefix", () => {
      expect(isDropboxAuthorizeUrl("https://evil.com/oauth2/authorize?foo")).toBe(false);
      expect(isDropboxAuthorizeUrl("https://www.dropbox.com/other")).toBe(false);
      expect(isDropboxAuthorizeUrl("http://www.dropbox.com/oauth2/authorize?x")).toBe(false);
    });

    it("rejects non-strings", () => {
      expect(isDropboxAuthorizeUrl(null)).toBe(false);
      expect(isDropboxAuthorizeUrl(123)).toBe(false);
    });
  });

  // --- decide ---
  describe("decide", () => {
    let storage;

    beforeEach(() => {
      storage = {
        _data: {},
        getItem(k) { return this._data[k] ?? null; },
        setItem(k, v) { this._data[k] = String(v); },
        removeItem(k) { delete this._data[k]; },
      };
    });

    it("returns 'start' when hash has valid return + authorize", () => {
      const hash = "#return=https%3A%2F%2Flocalhost%3A3000&authorize=https%3A%2F%2Fwww.dropbox.com%2Foauth2%2Fauthorize%3Fclient_id%3Dabc";

      const result = decide({ hash, search: "", storage });

      expect(result.action).toBe("start");
      expect(result.authorize).toBe("https://www.dropbox.com/oauth2/authorize?client_id=abc");
      expect(result.origin).toBe("https://localhost:3000");
      // Side effect: stored the origin
      expect(storage._data[STORAGE_KEY]).toBe("https://localhost:3000");
    });

    it("'start' rejects invalid return origin", () => {
      const hash = "#return=http%3A%2F%2Fevil.com&authorize=https%3A%2F%2Fwww.dropbox.com%2Foauth2%2Fauthorize%3Fclient_id%3Dabc";

      const result = decide({ hash, search: "", storage });
      // Invalid return falls through to idle
      expect(result.action).toBe("idle");
    });

    it("'start' rejects invalid authorize URL", () => {
      const hash = "#return=https%3A%2F%2Flocalhost%3A3000&authorize=https%3A%2F%2Fevil.com%2Fsteal";

      const result = decide({ hash, search: "", storage });
      expect(result.action).toBe("idle");
    });

    it("returns 'return' when search has code and storage has valid origin", () => {
      storage._data[STORAGE_KEY] = "https://mybox.local:3000";

      const result = decide({ hash: "", search: "?code=abc123&state=xyz", storage });

      expect(result.action).toBe("return");
      expect(result.url).toBe("https://mybox.local:3000/api/dropbox/callback?code=abc123&state=xyz");
      // Side effect: storage cleared
      expect(storage._data[STORAGE_KEY]).toBeUndefined();
    });

    it("'return' uses stored origin, never from search", () => {
      storage._data[STORAGE_KEY] = "https://real-instance.example.com";

      // Even if someone injected an origin in the search, the base URL is built from storage
      const result = decide({
        hash: "",
        search: "?code=abc&state=s",
        storage,
      });

      expect(result.action).toBe("return");
      // URL base comes from stored origin, not from any search parameter
      expect(result.url).toBe("https://real-instance.example.com/api/dropbox/callback?code=abc&state=s");
    });

    it("returns 'return' when search has error (not just code)", () => {
      storage._data[STORAGE_KEY] = "https://localhost:3000";

      const result = decide({ hash: "", search: "?error=access_denied&error_description=denied", storage });

      expect(result.action).toBe("return");
      expect(result.url).toBe("https://localhost:3000/api/dropbox/callback?error=access_denied&error_description=denied");
    });

    it("returns 'ask' when search has code but no stored origin", () => {
      const result = decide({ hash: "", search: "?code=abc&state=xyz", storage });

      expect(result.action).toBe("ask");
      expect(result.query).toBe("?code=abc&state=xyz");
    });

    it("returns 'ask' when storage is null (blocked)", () => {
      const result = decide({ hash: "", search: "?code=abc&state=xyz", storage: null });

      expect(result.action).toBe("ask");
    });

    it("returns 'idle' when no hash or search params", () => {
      const result = decide({ hash: "", search: "", storage });

      expect(result.action).toBe("idle");
    });

    it("returns 'idle' when hash is empty and search has no code/error", () => {
      const result = decide({ hash: "", search: "?foo=bar", storage });

      expect(result.action).toBe("idle");
    });

    it("handles storage that throws on access", () => {
      const throwStorage = {
        getItem() { throw new Error("blocked"); },
        setItem() { throw new Error("blocked"); },
        removeItem() { throw new Error("blocked"); },
      };

      // Start: should still return start action but storage.setItem fails silently
      const hash = "#return=https%3A%2F%2Flocalhost%3A3000&authorize=https%3A%2F%2Fwww.dropbox.com%2Foauth2%2Fauthorize%3Fclient_id%3Dabc";
      const result = decide({ hash, search: "", storage: throwStorage });
      expect(result.action).toBe("start");

      // Return: falls to 'ask' since getItem throws
      const result2 = decide({ hash: "", search: "?code=abc", storage: throwStorage });
      expect(result2.action).toBe("ask");
    });
  });
});

describe("relay.js parseParams robustness", () => {
  it("skips pairs with malformed percent-encoding instead of throwing", () => {
    expect(() => parseParams("#return=%E0%A4%A&authorize=ok")).not.toThrow();
    expect(parseParams("#return=%E0%A4%A&authorize=ok")).toEqual({ authorize: "ok" });
  });

  it("ignores empty segments", () => {
    expect(parseParams("?a=1&&b=2&")).toEqual({ a: "1", b: "2" });
  });

  it("decide() does not throw on a malformed callback query", () => {
    const storage = new Map();
    const store = { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
    expect(() => decide({ hash: "", search: "?code=%ZZ&state=x", storage: store })).not.toThrow();
  });
});
