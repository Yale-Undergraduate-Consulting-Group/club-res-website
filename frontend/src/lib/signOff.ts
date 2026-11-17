import type { Settings } from '../api';

/** A member's sign-off as fields, plus what the server adds on its own (GET /api/settings). */
export type SignOffFields = {
  name: string; pronouns: string; role: string; organization: string; linkedin: string; phone: string;
  logoUrl: string;
  /** The club logo a blank logo signs with; '' when the site has no public address. */
  logoDefault: string;
  notice: string;
};

export const EMPTY_SIGN_OFF: SignOffFields = {
  name: '', pronouns: '', role: '', organization: 'Yale Undergraduate Consulting Group',
  linkedin: '', phone: '', logoUrl: '', logoDefault: '', notice: '',
};

/** The fields as stored, with the account's name when the sign-off names nobody. */
export function signOffFrom(s: Settings, accountName = ''): SignOffFields {
  return {
    name: s.sign_off_name || accountName,
    pronouns: s.sign_off_pronouns || '',
    role: s.sign_off_role || '',
    organization: s.sign_off_organization || EMPTY_SIGN_OFF.organization,
    linkedin: s.sign_off_linkedin || '',
    phone: s.sign_off_phone || '',
    logoUrl: s.sign_off_logo_url || '',
    logoDefault: s.sign_off_logo_default || '',
    notice: s.sign_off_notice || '',
  };
}
