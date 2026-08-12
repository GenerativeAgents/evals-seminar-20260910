export const VARIANTS = ["baseline", "improvement-1", "improvement-2"] as const;

export type Variant = (typeof VARIANTS)[number];

export function isVariant(value: string): value is Variant {
  return (VARIANTS as readonly string[]).includes(value);
}
