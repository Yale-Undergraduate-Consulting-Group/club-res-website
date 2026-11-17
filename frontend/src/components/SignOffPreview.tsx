import type { SignOffFields } from '../lib/signOff';

/** The sign-off as the email draws it (backend/app/services/email_body.py):
 *  "--", the logo, a thin divider, the details, then the notice. */
export default function SignOffPreview({ signOff, className = '', logoClassName = 'w-[72px]' }: {
  signOff: SignOffFields; className?: string; logoClassName?: string;
}) {
  const logo = signOff.logoUrl || signOff.logoDefault;
  return (
    <div className={className} data-testid="sign-off-preview">
      <div>--</div>
      <div className="mt-2 flex items-center gap-3">
        {logo && <img src={logo} alt="YUCG" className={`${logoClassName} h-auto object-contain`} />}
        <div className={logo ? 'border-l border-slate-300 pl-3' : ''}>
          <div className="font-bold text-deep-navy dark:text-slate-100">
            {signOff.name || 'Your name'} {signOff.pronouns && <em className="font-normal">({signOff.pronouns})</em>}
          </div>
          {signOff.role && <div>{signOff.role}</div>}
          <div>{signOff.organization}</div>
          {(signOff.linkedin || signOff.phone) && (
            <div className="mt-0.5 text-deep-navy dark:text-slate-100">
              {signOff.linkedin ? 'LinkedIn' : ''}{signOff.linkedin && signOff.phone ? ' | ' : ''}{signOff.phone}
            </div>
          )}
        </div>
      </div>
      {signOff.notice && <p className="mt-3 text-[11px] italic leading-snug text-slate-500">{signOff.notice}</p>}
    </div>
  );
}
