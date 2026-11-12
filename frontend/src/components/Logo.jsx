import React from 'react';

/** The AutoGrade mark: an answer sheet with a teacher's red-pen tick across it. */
export default function Logo({ size = 36, className = '' }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      className={`logo-mark shrink-0 ${className}`}
      aria-hidden="true"
    >
      <path
        d="M9.5 3.5h15.2l8.3 8.3v23.2a2 2 0 0 1-2 2H9.5a2 2 0 0 1-2-2V5.5a2 2 0 0 1 2-2Z"
        fill="var(--surface)"
        stroke="var(--ink)"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path d="M24.7 3.5v6.3a2 2 0 0 0 2 2H33" fill="none" stroke="var(--ink)" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M12 15.5h9M12 20.5h16M12 25.5h16M12 30.5h11" stroke="var(--line-strong)" strokeWidth="1.6" strokeLinecap="round" />
      <path
        d="M13.2 23.2c1.6 1.4 3.1 3.3 4.4 5.6C21.4 21 25.8 15.4 33.6 10.4"
        fill="none"
        stroke="var(--bad)"
        strokeWidth="3.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
