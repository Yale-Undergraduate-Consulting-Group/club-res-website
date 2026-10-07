import { afterEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_PREFERENCES, getStoredPreferences, savePreferences } from './userPreferences';

describe('stored preferences', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('round-trips saved choices', () => {
    savePreferences({ accent: '#336699', compact: true, fontSize: 'large', reduceMotion: true });
    expect(getStoredPreferences()).toEqual({ accent: '#336699', compact: true, fontSize: 'large', reduceMotion: true });
  });

  it('replaces an unknown stored font size with the default', () => {
    localStorage.setItem('yucg_font_size', 'huge');
    expect(getStoredPreferences().fontSize).toBe('medium');
  });

  it('keeps working when browser storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError'); });
    expect(() => savePreferences({ compact: true })).not.toThrow();
    expect(getStoredPreferences()).toEqual(DEFAULT_PREFERENCES);
  });
});
