import "./TrafficIndicator.css";

interface TrafficIndicatorProps {
  level: string;
}

const TrafficIndicator = ({ level }: TrafficIndicatorProps) => {
  const getColor = () => {
    switch (level.toLowerCase()) {
      case "light":
        return "#4CAF50";
      case "moderate":
        return "#FFB300";
      case "busy":
        return "#E53935";
      default:
        return "#9E9E9E";
    }
  };

  return (
    <div className="traffic-indicator">
      <div
        className="indicator-dot"
        style={{ backgroundColor: getColor() }}
      ></div>
      <span>{level}</span>
    </div>
  );
}

export default TrafficIndicator;