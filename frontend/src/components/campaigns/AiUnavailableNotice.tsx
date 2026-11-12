import { useState } from 'react';
import { Link } from 'react-router-dom';
import { AI_DRAFTING_UNAVAILABLE } from '../../lib/aiDrafting';
import { Button, Notice } from '../ui/Primitives';
import QueueOutreach from './QueueOutreach';

/**
 * Shown where AI drafting failed because the model is unavailable. Instead of
 * a raw provider error it says so and offers the way forward that needs no
 * model: pick a saved template and queue these people with it. With nobody
 * chosen yet there is nothing to queue, so it points at the templates instead.
 */
export default function AiUnavailableNotice({ contactIds, label }: { contactIds: number[]; label: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Notice tone="warning">
      <span>{AI_DRAFTING_UNAVAILABLE} </span>
      {contactIds.length > 0 ? (
        <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>Use a template</Button>
      ) : (
        <Link className="ui-button ui-button--secondary ui-button--sm" to="/outreach?view=resources">See the templates</Link>
      )}
      {open && <QueueOutreach source={{ label, contactIds }} onClose={() => setOpen(false)} />}
    </Notice>
  );
}
