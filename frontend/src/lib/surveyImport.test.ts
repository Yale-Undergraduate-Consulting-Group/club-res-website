import { describe, expect, it } from 'vitest';
import { parseSurveyText, questionsMissingChoices } from './surveyImport';

describe('parseSurveyText', () => {
  it('reads the documented format: name, tags, choices, slider range, comments', () => {
    const draft = parseSurveyText(`# comment
Survey: Coffee Near Campus
1. What year are you? [single]
- First-year
- Senior
2. Where do you get coffee? [multi]
- Starbucks
3. Location beats price. [agree]
4. Latte price, dollars [slider 2-8]
Q5) Anything else? [text]`);
    expect(draft.name).toBe('Coffee Near Campus');
    expect(draft.questions).toEqual([
      { type: 'single_choice', text: 'What year are you?', choices: ['First-year', 'Senior'] },
      { type: 'multi_choice', text: 'Where do you get coffee?', choices: ['Starbucks'] },
      { type: 'likert', text: 'Location beats price.' },
      { type: 'slider', text: 'Latte price, dollars', min: 2, max: 8 },
      { type: 'text', text: 'Anything else?' },
    ]);
  });

  it('infers the type when a question has no tag, and joins wrapped wording', () => {
    const draft = parseSurveyText('1. How often do you\nvisit?\n- Daily\n2. Why?');
    expect(draft.name).toBe('Imported survey');
    expect(draft.questions[0]).toEqual({ type: 'single_choice', text: 'How often do you visit?', choices: ['Daily'] });
    expect(draft.questions[1]).toEqual({ type: 'text', text: 'Why?' });
  });

  it('flags tagged choice questions that have no choices', () => {
    const draft = parseSurveyText('1. Pick one [single]\n2. Rate it [slider]');
    expect(questionsMissingChoices(draft).map((q) => q.text)).toEqual(['Pick one']);
    expect(draft.questions[1]).toMatchObject({ min: 0, max: 10 });
  });
});
