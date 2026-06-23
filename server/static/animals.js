/* Shared cute-animal SVGs for BrioCare (Brightseed palette: teal / coral / amber).
   Each fills its container. Use `brioRandomAnimal()` for a random buddy, or
   `brioAnimal(name)` for a specific one. Loaded by kid / therapist / landing pages. */
(function () {
  const A = {
    hummingbird: `<svg viewBox="0 0 130 120" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M46 72 L13 60 L27 88 Z" fill="#f37a8c"/>
      <path d="M60 60 Q30 40 13 52 Q36 56 54 74 Z" fill="#ec7a87"/>
      <ellipse cx="62" cy="69" rx="25" ry="18" fill="#39a79e"/>
      <ellipse cx="57" cy="76" rx="17" ry="10" fill="#57c2b0"/>
      <circle cx="87" cy="50" r="15" fill="#57c2b0"/>
      <path d="M87 35 A15 15 0 0 1 101 46 Q88 48 79 56 A15 15 0 0 1 87 35 Z" fill="#39a79e"/>
      <path d="M101 50 L130 44 L101 55 Z" fill="#226468"/>
      <circle cx="91" cy="47" r="2.8" fill="#1f3b3d"/>
      <path d="M66 60 Q76 24 97 25 Q90 49 72 66 Z" fill="#f1697c"/>
      <circle cx="122" cy="39" r="3.4" fill="#f1697c"/>
    </svg>`,
    fox: `<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M32 40 L20 14 46 32 Z" fill="#f1697c"/>
      <path d="M88 40 L100 14 74 32 Z" fill="#f1697c"/>
      <path d="M35 36 L28 22 43 33 Z" fill="#fde7ea"/>
      <path d="M85 36 L92 22 77 33 Z" fill="#fde7ea"/>
      <circle cx="60" cy="62" r="33" fill="#f1697c"/>
      <ellipse cx="60" cy="74" rx="21" ry="16" fill="#fde7ea"/>
      <circle cx="49" cy="58" r="4.5" fill="#1f3b3d"/>
      <circle cx="71" cy="58" r="4.5" fill="#1f3b3d"/>
      <circle cx="60" cy="70" r="4" fill="#1f3b3d"/>
      <path d="M53 79 Q60 85 67 79" stroke="#1f3b3d" stroke-width="2.4" fill="none" stroke-linecap="round"/>
    </svg>`,
    bunny: `<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <rect x="43" y="8" width="13" height="44" rx="6.5" fill="#fff" stroke="#39a79e" stroke-width="3"/>
      <rect x="64" y="8" width="13" height="44" rx="6.5" fill="#fff" stroke="#39a79e" stroke-width="3"/>
      <circle cx="60" cy="72" r="32" fill="#fff" stroke="#39a79e" stroke-width="3"/>
      <circle cx="49" cy="68" r="4" fill="#1f3b3d"/>
      <circle cx="71" cy="68" r="4" fill="#1f3b3d"/>
      <circle cx="60" cy="78" r="4" fill="#f1697c"/>
      <path d="M53 86 Q60 92 67 86" stroke="#1f3b3d" stroke-width="2.4" fill="none" stroke-linecap="round"/>
    </svg>`,
    bear: `<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <circle cx="35" cy="42" r="14" fill="#e8902c"/>
      <circle cx="85" cy="42" r="14" fill="#e8902c"/>
      <circle cx="35" cy="42" r="7" fill="#fbe7c9"/>
      <circle cx="85" cy="42" r="7" fill="#fbe7c9"/>
      <circle cx="60" cy="64" r="33" fill="#e8902c"/>
      <ellipse cx="60" cy="76" rx="19" ry="14" fill="#fbe7c9"/>
      <circle cx="49" cy="60" r="4.5" fill="#1f3b3d"/>
      <circle cx="71" cy="60" r="4.5" fill="#1f3b3d"/>
      <circle cx="60" cy="72" r="4.5" fill="#1f3b3d"/>
      <path d="M53 81 Q60 87 67 81" stroke="#1f3b3d" stroke-width="2.4" fill="none" stroke-linecap="round"/>
    </svg>`,
    owl: `<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M30 42 Q30 20 48 28 Z" fill="#39a79e"/>
      <path d="M90 42 Q90 20 72 28 Z" fill="#39a79e"/>
      <ellipse cx="60" cy="64" rx="35" ry="37" fill="#39a79e"/>
      <ellipse cx="60" cy="74" rx="23" ry="24" fill="#57c2b0"/>
      <circle cx="47" cy="56" r="13" fill="#fff"/>
      <circle cx="73" cy="56" r="13" fill="#fff"/>
      <circle cx="47" cy="56" r="5.5" fill="#1f3b3d"/>
      <circle cx="73" cy="56" r="5.5" fill="#1f3b3d"/>
      <path d="M54 64 L60 73 66 64 Z" fill="#e8902c"/>
    </svg>`,
  };
  window.BRIO_ANIMALS = A;
  window.brioAnimal = function (name) { return A[name] || A.hummingbird; };
  window.brioRandomAnimal = function () {
    const keys = Object.keys(A);
    return A[keys[Math.floor(Math.random() * keys.length)]];
  };
})();
