"use client";

import React from "react";
import { Heart } from "lucide-react";
import { KoreanLocation } from "@/types";
import { useSavedToggle } from "@/lib/useUser";

interface Props {
  location: KoreanLocation;
  /** "overlay" sits on a photo; "inline" sits on a light surface. */
  variant?: "overlay" | "inline";
  showLabel?: boolean;
  className?: string;
  locale?: "ko" | "en";
}

export const FavoriteButton: React.FC<Props> = ({
  location,
  variant = "overlay",
  showLabel = false,
  className = "",
  locale = "ko",
}) => {
  const [saved, toggle] = useSavedToggle(location);

  const base =
    variant === "overlay"
      ? "bg-black/35 backdrop-blur-sm hover:bg-black/50 text-white"
      : "bg-white border border-gray-200 hover:border-gray-300 text-gray-700 shadow-sm";

  return (
    <button
      type="button"
      aria-pressed={saved}
      aria-label={saved ? (locale === "en" ? "Remove from saved" : "저장 취소") : (locale === "en" ? "Save" : "저장하기")}
      title={saved ? (locale === "en" ? "Remove from saved" : "저장 취소") : (locale === "en" ? "Save" : "저장하기")}
      onClick={(e) => {
        // Cards are wrapped in a Link; without this the heart navigates.
        e.preventDefault();
        e.stopPropagation();
        toggle();
      }}
      className={`flex items-center gap-1.5 rounded-full transition-all active:scale-90 ${base} ${
        showLabel ? "px-4 py-2.5 text-sm font-semibold" : "p-2"
      } ${className}`}
    >
      <Heart
        className={`w-5 h-5 transition-colors ${
          saved
            ? "fill-rose-500 text-rose-500"
            : variant === "overlay"
            ? "text-white"
            : "text-gray-500"
        }`}
      />
      {showLabel && <span>{saved ? (locale === "en" ? "Saved" : "저장됨") : (locale === "en" ? "Save" : "저장")}</span>}
    </button>
  );
};
