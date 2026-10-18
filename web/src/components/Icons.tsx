// Stroke icons, 16px, currentColor. Stroke 2 beside 600-weight text, 1.5 beside 400.
type P = { strokeWidth?: number };

export const ArrowRight = ({ strokeWidth = 2 }: P) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M3 8h10M9 4l4 4-4 4" />
  </svg>
);
export const External = ({ strokeWidth = 2 }: P) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M9 3h4v4M13 3L7.5 8.5M11 9.5V13H3V5h3.5" />
  </svg>
);
export const Plus = ({ strokeWidth = 1.5 }: P) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" aria-hidden="true">
    <path d="M8 3v10M3 8h10" />
  </svg>
);
export const Minus = ({ strokeWidth = 1.5 }: P) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" aria-hidden="true">
    <path d="M3 8h10" />
  </svg>
);
export const Phone = ({ strokeWidth = 1.5 }: P) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={strokeWidth} strokeLinejoin="round" aria-hidden="true">
    <path d="M5.5 2.5l1.5 3-1.5 1a7 7 0 0 0 3.5 3.5l1-1.5 3 1.5v2a1.5 1.5 0 0 1-1.6 1.5A11 11 0 0 1 2 4.1 1.5 1.5 0 0 1 3.5 2.5z" />
  </svg>
);
export const Pointer = () => (
  <svg viewBox="0 0 16 16" aria-hidden="true">
    <path d="M3 2l9.5 6.2-4.2.8 2.4 4.6-1.6.8-2.4-4.6L3.5 12.6z" fill="#0A0A0A" stroke="#fff" strokeWidth="1" strokeLinejoin="round" />
  </svg>
);
