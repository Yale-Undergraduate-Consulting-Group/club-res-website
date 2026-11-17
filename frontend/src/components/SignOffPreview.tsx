import type { SignOffFields } from '../lib/signOff';

/** The sign-off as the email draws it (backend/app/services/email_body.py):
 *  "--", the logo, a thin divider, the details in black Arial, then the notice. */
export default function SignOffPreview({ signOff, className = '', logoClassName = 'w-[72px]' }: {
  signOff: SignOffFields; className?: string; logoClassName?: string;
}) {
  const logo = signOff.logoUrl || signOff.logoDefault;
  return (
    <div className={className} style={{ fontFamily: 'Arial, Helvetica, sans-serif' }} data-testid="sign-off-preview">
      <div className="text-[var(--text-primary)]">--</div>
      <div className="mt-2 flex items-center gap-3">
        {logo && <img src={logo} alt="YUCG" className={`${logoClassName} h-auto object-contain`} />}
        <div className={`text-[var(--text-primary)] ${logo ? 'border-l border-slate-300 pl-3' : ''}`}>
          <div className="font-bold">
            {signOff.name || 'Your name'}{signOff.pronouns && <> (<em>{signOff.pronouns}</em>)</>}
          </div>
          {signOff.role && <div>{signOff.role}</div>}
          <div>{signOff.organization}</div>
          {(signOff.linkedin || signOff.phone) && (
            <div>
              {signOff.linkedin && <a href={signOff.linkedin} target="_blank" rel="noreferrer" className="text-[var(--link)] underline">LinkedIn</a>}
              {signOff.linkedin && signOff.phone ? ' | ' : ''}{signOff.phone}
            </div>
          )}
        </div>
      </div>
      {signOff.notice && <p className="mt-3 text-[11px] italic leading-snug text-slate-500">{signOff.notice}</p>}
    </div>
  );
}
