import React from "react";
import {
  Sparkles,
  Building2,
  TreePine,
  Coffee,
  Castle,
  Home,
  Store,
} from "lucide-react";

interface CategoryBarProps {
  locale?: "ko" | "en";
  selectedCategory: string;
  onSelectCategory: (c: string) => void;
}

export const CategoryBar: React.FC<CategoryBarProps> = ({
  locale = "ko",
  selectedCategory,
  onSelectCategory,
}) => {
  const categories = [
    { name: "전체", label: "All", icon: Sparkles },
    { name: "모던 스튜디오", label: "Modern studios", icon: Building2 },
    { name: "전통 한옥", label: "Traditional houses", icon: Castle },
    { name: "자연/야외", label: "Nature / outdoor", icon: TreePine },
    { name: "빈티지/창고", label: "Vintage / warehouse", icon: Store },
    { name: "럭셔리 하우스", label: "Luxury houses", icon: Home },
    { name: "카페/갤러리", label: "Cafés / galleries", icon: Coffee },
  ];

  return (
    <div className="flex items-center space-x-6 overflow-x-auto pb-4 pt-2 scrollbar-none border-b border-gray-200">
      {categories.map((cat) => (
        <button
          key={cat.name}
          onClick={() => onSelectCategory(cat.name)}
          className={`flex flex-col items-center space-y-2 min-w-max pb-3 border-b-2 transition-all ${
            selectedCategory === cat.name
              ? "border-gray-900 text-gray-900"
              : "border-transparent text-gray-500 hover:text-gray-900 hover:border-gray-300"
          }`}
        >
          <cat.icon
            className={`w-6 h-6 ${
              selectedCategory === cat.name ? "text-gray-900" : "text-gray-500"
            }`}
          />
          <span className="text-xs font-semibold">{locale === "en" ? cat.label : cat.name}</span>
        </button>
      ))}
    </div>
  );
};
