import React from "react";
import { Search, SlidersHorizontal, Sparkles, RefreshCw } from "lucide-react";

interface FilterBarProps {
  searchQuery: string;
  onSearchChange: (v: string) => void;
  selectedRegion: string;
  onRegionChange: (v: string) => void;
  maxPrice: number;
  onMaxPriceChange: (v: number) => void;
  windowDir: string;
  onWindowDirChange: (v: string) => void;
  minParking: number;
  onMinParkingChange: (v: number) => void;
  onResetFilters: () => void;
  onTriggerLiveCrawl?: () => void;
  isCrawling?: boolean;
}

export const FilterBar: React.FC<FilterBarProps> = ({
  searchQuery,
  onSearchChange,
  selectedRegion,
  onRegionChange,
  maxPrice,
  onMaxPriceChange,
  windowDir,
  onWindowDirChange,
  minParking,
  onMinParkingChange,
  onResetFilters,
  onTriggerLiveCrawl,
  isCrawling = false,
}) => {
  return (
    <div className="flex flex-wrap items-center gap-3 py-2">
      {/* Search Input */}
      <div className="relative flex-grow max-w-md">
        <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
          <Search className="h-4 w-4 text-gray-400" />
        </div>
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="지역, 공간 유형 검색"
          className="w-full pl-10 pr-4 py-3 bg-white border border-gray-300 rounded-full text-sm font-medium text-gray-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:border-transparent transition-all"
        />
      </div>

      {/* Region Pills */}
      <div className="flex items-center space-x-1.5 bg-gray-100 p-1.5 rounded-full border border-gray-200">
        {["전체", "서울", "경기", "제주"].map((r) => (
          <button
            key={r}
            onClick={() => onRegionChange(r)}
            className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors ${
              selectedRegion === r
                ? "bg-white text-gray-900 shadow-sm border border-gray-200 font-semibold"
                : "text-gray-600 hover:text-gray-900"
            }`}
          >
            {r}
          </button>
        ))}
      </div>

      {/* Live Parallel Crawl Trigger */}
      {onTriggerLiveCrawl && (
        <button
          onClick={onTriggerLiveCrawl}
          disabled={isCrawling}
          className="flex items-center space-x-1.5 px-4 py-3 bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-700 hover:to-indigo-600 text-white rounded-full text-sm font-semibold shadow-sm transition-all disabled:opacity-60"
        >
          {isCrawling ? (
            <>
              <RefreshCw className="w-4 h-4 animate-spin" />
              <span>실제 매물 수집 중...</span>
            </>
          ) : (
            <>
              <Sparkles className="w-4 h-4" />
              <span>실제 매물 수집</span>
            </>
          )}
        </button>
      )}

      <button
        onClick={onResetFilters}
        className="flex items-center space-x-2 px-4 py-3 bg-white border border-gray-300 rounded-full text-sm font-medium text-gray-700 hover:border-gray-400 transition-colors shadow-sm ml-auto"
      >
        <SlidersHorizontal className="w-4 h-4" />
        <span>필터 초기화</span>
      </button>
    </div>
  );
};
