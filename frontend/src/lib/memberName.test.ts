import { describe, expect, it } from 'vitest';
import { memberFirstName } from './memberName';

describe('memberFirstName', () => {
  it.each([
    [{ name: 'Andre Costa', email: 'andre.h.costa@yale.edu' }, 'Andre'],
    [{ name: '  ', email: 'andre.h.costa@yale.edu' }, 'Andre'],
    [{ name: 'andre.h.costa', email: 'andre.h.costa@yale.edu' }, 'Andre'],
    [{ email: 'ana_lee@yale.edu' }, 'Ana'],
    [{ email: 'ben@yale.edu' }, 'Ben'],
    [{ email: '.x@yale.edu' }, '.x'],
  ])('%o is %s', (user, expected) => expect(memberFirstName(user)).toBe(expected));
});
