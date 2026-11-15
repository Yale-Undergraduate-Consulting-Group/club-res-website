import type { ReactNode } from 'react';
import { FUNCTIONS, fitLevel, functionOf, isTooSenior, looksLikePerson, yaleOf } from '../../lib/recipients';
import { matchReason, sourceOf, type PersonLike, type Wanted } from '../../lib/peopleSheet';

/**
 * What every person row says, whether they are on file or only found: who
 * they are (name, current title, company), why they do or do not match the
 * level the member chose, and where they came from with a link to look.
 */

export function PersonHeadline({ name, person, company }: { name: string; person: PersonLike; company: string }) {
  return (
    <>
      <span className="font-medium text-deep-navy">{name}</span>
      <span className="text-slate-500"> · {person.title || 'no title'}</span>
      <span className="text-slate-500"> · {person.company || company}</span>
    </>
  );
}

export function PersonWhy({ person, wanted, children }: { person: PersonLike; wanted: Wanted; children?: ReactNode }) {
  const source = sourceOf(person);
  const matches = wanted.has(fitLevel(person));
  const yale = yaleOf(person);
  const fn = functionOf(person);
  return (
    <span className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
      {/* The title outranks the stored level: a CFO whose filing title reads as
          working level is still a CFO. */}
      {!looksLikePerson(person) ? (
        <span className="bg-pale-sky/60 px-2 py-0.5 text-deep-navy"
              title="This name looks like page text a search picked up, not a person, so it is not ticked by default">
          Not a person? Check the name
        </span>
      ) : isTooSenior(person) ? (
        <span className="bg-pale-sky/60 px-2 py-0.5 text-deep-navy"
              title="Chief officers, presidents and senior vice presidents rarely answer a student's cold email, so they are not ticked by default">
          Very senior · rarely replies
        </span>
      ) : (
        <span className={`px-2 py-0.5 ${matches ? 'bg-pale-sky/60 text-deep-navy' : 'bg-pale-sky/60 text-deep-navy'}`}
              data-testid="match-reason" data-match={matches || undefined}>
          {matchReason(person, wanted)}
        </span>
      )}
      {yale && (
        <span className="ui-status ui-status--info" data-testid="yale-badge"
              title={`Tied to Yale by what the search read: ${yale}`} aria-label={`Yale: ${yale}`}>
          Yale
        </span>
      )}
      {fn && (
        <span className="bg-pale-sky/60 px-2 py-0.5 text-deep-navy" data-testid="person-function">
          {FUNCTIONS.find((f) => f.id === fn)?.label}
        </span>
      )}
      {children}
      <span data-testid="person-source">
        Source: {source.label}
        {source.link && (
          <>
            {' · '}
            <a href={source.link.href} target="_blank" rel="noopener noreferrer" className="underline">{source.link.text}</a>
          </>
        )}
      </span>
    </span>
  );
}
