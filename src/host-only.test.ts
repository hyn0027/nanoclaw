import { describe, expect, it } from 'vitest';

import { HOST_ONLY_NOTE, isHostOnlyCard, markHostOnly } from './host-only.js';

describe('host-only note', () => {
  it('ends a text message with the note, once', () => {
    const once = markHostOnly(JSON.stringify({ text: 'hello' }));
    expect(JSON.parse(once).text).toBe(`hello\n\n${HOST_ONLY_NOTE}`);
    expect(markHostOnly(once)).toBe(once);
  });

  it("ends a card's question with the note and remembers the card", () => {
    const card = { type: 'ask_question', questionId: 'q-1', title: 'T', question: 'Q?', options: [] };
    const marked = JSON.parse(markHostOnly(JSON.stringify(card)));
    expect(marked.question).toBe(`Q?\n\n${HOST_ONLY_NOTE}`);
    expect(marked.title).toBe('T');
    expect(isHostOnlyCard('q-1')).toBe(true);
    expect(isHostOnlyCard('q-2')).toBe(false);
  });

  it('sends a message the agent is told about without the note, and drops the flag', () => {
    const card = { type: 'ask_question', questionId: 'q-seen', title: 'T', question: 'Q?', seenByAgent: true };
    const sent = JSON.parse(markHostOnly(JSON.stringify(card)));
    expect(sent).toEqual({ type: 'ask_question', questionId: 'q-seen', title: 'T', question: 'Q?' });
    expect(isHostOnlyCard('q-seen')).toBe(false);
  });

  it('leaves other content alone', () => {
    expect(markHostOnly('not json')).toBe('not json');
    const files = JSON.stringify({ files: ['a.png'] });
    expect(markHostOnly(files)).toBe(files);
  });
});
