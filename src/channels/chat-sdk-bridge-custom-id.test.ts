import { describe, expect, it } from 'vitest';

import { parseCardCustomId } from './chat-sdk-bridge.js';

describe('parseCardCustomId', () => {
  it('splits the Chat SDK `<id>\\n<value>` encoding (the Discord approval-click bug)', () => {
    expect(parseCardCustomId('ncq:appr-1794325435456-yelmfw:0\n0')).toEqual({
      questionId: 'appr-1794325435456-yelmfw',
      tail: '0',
      buttonValue: '0',
    });
  });

  it('still reads a bare custom_id with no value part', () => {
    expect(parseCardCustomId('ncq:q1:2')).toEqual({ questionId: 'q1', tail: '2', buttonValue: undefined });
  });

  it('ignores ids that are not ask_question card buttons', () => {
    expect(parseCardCustomId('other:x:1\n1')).toEqual({});
    expect(parseCardCustomId(undefined)).toEqual({});
  });
});
