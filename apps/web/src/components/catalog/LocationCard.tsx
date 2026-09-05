import React from "react";
import { KoreanLocation } from "@/types";
import { Maximize2, Camera } from "lucide-react";
import { resolveImageUrl } from "@/lib/api";
import { FavoriteButton } from "@/components/common/FavoriteButton";

interface LocationCardProps {
  location: KoreanLocation;
}

export const LocationCard: React.FC<LocationCardProps> = ({ location }) => {
  const cleanName = location.name.replace(/\[.*?\]/, "").trim();

  // A reference record is a real place films have used, not a rentable listing.
  // Marking it is the whole reason the catalogue tracks listing_kind: showing
  // it like a bookable venue would imply an availability nobody verified.
  const isReference = location.listing_kind === "reference";

  return (
    <div className="relative flex flex-col space-y-3 cursor-pointer group">
      {/* Aspect Ratio Image Container */}
      <div className="relative aspect-[4/5] w-full overflow-hidden rounded-lg bg-gray-100 shadow-sm border border-gray-200">
        <img
          src={resolveImageUrl(location.images[0])}
          alt={cleanName}
          loading="lazy"
          className="h-full w-full object-cover transition-transform duration-700 group-hover:scale-105"
        />

        {/* Saving is the one action available from the grid, so it takes the
            corner a user reaches for. The NEW badge moves down-left to make
            room rather than sharing the spot. */}
        <div className="absolute top-2.5 right-2.5 z-10">
          <FavoriteButton location={location} />
        </div>

        {/* Fresh find — server-derived, drops off 72h after first sighting */}
        {location.is_new && (
          <div className="absolute bottom-3 left-3 bg-rose-600 px-2 py-1 rounded-sm text-[11px] font-bold tracking-wide text-white shadow-md">
            신규
          </div>
        )}

        {/* Availability, when it is not the ordinary case */}
        {isReference && (
          <div className="absolute bottom-3 right-3 bg-slate-900/85 backdrop-blur-md px-2.5 py-1 rounded-sm text-[11px] font-bold tracking-wide text-white shadow-sm">
            촬영 기록 · 대관 미확인
          </div>
        )}

      </div>

      {/* Details */}
      <div className="flex flex-col space-y-1.5 px-0.5">
        <div className="flex items-center justify-between">
          <h3 className="font-bold text-slate-900 truncate text-base">
            {location.region}
          </h3>
        </div>

        <p className="text-sm font-medium text-slate-700 truncate">{cleanName}</p>

        <div className="flex items-center space-x-3 text-xs text-slate-500 font-medium">
          {location.specs.area_pyeong > 0 && (
            <div className="flex items-center space-x-1">
              <Maximize2 className="w-3.5 h-3.5" />
              <span>{location.specs.area_pyeong}평</span>
            </div>
          )}
          <div className="flex items-center space-x-1">
            <Camera className="w-3.5 h-3.5" />
            <span>{location.specs.window_direction.split(" ")[0]}</span>
          </div>
        </div>

        <div className="pt-2 flex items-baseline space-x-1 border-t border-gray-100">
          {location.price_per_hour > 0 ? (
            <>
              <span className="font-bold text-slate-900 text-base">
                ₩{location.price_per_hour.toLocaleString()}
              </span>
              <span className="text-sm font-medium text-slate-500">/ 시간</span>
            </>
          ) : isReference ? (
            // No price is known and none is invented.
            <span className="text-sm font-semibold text-slate-500">대관 조건 직접 확인 필요</span>
          ) : (
            <span className="text-sm font-semibold text-slate-500">가격 원본 페이지 확인</span>
          )}
        </div>
      </div>
    </div>
  );
};
