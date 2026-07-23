const DECIMAL_SEQUENCE = /^(0|[1-9][0-9]{0,18})$/;
export const MAX_SEQUENCE = 9_223_372_036_854_775_807n;

export class SequenceError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SequenceError";
  }
}

export function parseSequence(value: string): bigint {
  if (!DECIMAL_SEQUENCE.test(value)) {
    throw new SequenceError(`非法事件 sequence：${value}`);
  }
  const sequence = BigInt(value);
  if (sequence > MAX_SEQUENCE) {
    throw new SequenceError(`事件 sequence 超出 PostgreSQL BIGINT 范围：${value}`);
  }
  return sequence;
}

export function compareSequence(left: string, right: string): number {
  const leftValue = parseSequence(left);
  const rightValue = parseSequence(right);
  return leftValue < rightValue ? -1 : leftValue > rightValue ? 1 : 0;
}

export function nextSequence(value: string): string {
  const next = parseSequence(value) + 1n;
  if (next > MAX_SEQUENCE) {
    throw new SequenceError(`事件 sequence 已达到 PostgreSQL BIGINT 上限：${value}`);
  }
  return next.toString();
}
