import React from "react";

interface AvatarProps {
  src?: string;
  alt?: string;
  initials?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

const SIZE_CLASSES = {
  sm: "w-8 h-8 text-xs",
  md: "w-10 h-10 text-sm",
  lg: "w-14 h-14 text-base",
};

export function Avatar({ src, alt, initials, size = "md", className = "" }: AvatarProps) {
  const sizeClass = SIZE_CLASSES[size];
  return (
    <div className={`${sizeClass} rounded-full overflow-hidden bg-primary/10 text-primary flex items-center justify-center font-bold shrink-0 ${className}`}>
      {src ? (
        <img src={src} alt={alt ?? ""} className="w-full h-full object-cover" />
      ) : (
        <span>{initials ?? "?"}</span>
      )}
    </div>
  );
}
