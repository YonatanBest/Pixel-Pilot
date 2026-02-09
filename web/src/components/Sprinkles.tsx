import { useEffect, useState } from 'react';
import './Sprinkles.css';

interface Sprinkle {
  id: number;
  x: number;
  y: number;
  size: number;
  opacity: number;
  color: 'primary' | 'secondary';
}

export const Sprinkles = () => {
  const [sprinkles, setSprinkles] = useState<Sprinkle[]>([]);

  useEffect(() => {
    const generateSprinkles = () => {
      const count = 40;
      const newSprinkles: Sprinkle[] = [];
      
      for (let i = 0; i < count; i++) {
        newSprinkles.push({
          id: i,
          x: Math.random() * 100,
          y: Math.random() * 100,
          size: Math.random() * 3 + 1,
          opacity: Math.random() * 0.15 + 0.05,
          color: Math.random() > 0.5 ? 'primary' : 'secondary',
        });
      }
      
      setSprinkles(newSprinkles);
    };

    generateSprinkles();
  }, []);

  return (
    <div className="sprinkles-container">
      {sprinkles.map((sprinkle) => (
        <div
          key={sprinkle.id}
          className={`sprinkle-dot sprinkle-${sprinkle.color}`}
          style={{
            left: `${sprinkle.x}%`,
            top: `${sprinkle.y}%`,
            width: `${sprinkle.size}px`,
            height: `${sprinkle.size}px`,
            opacity: sprinkle.opacity,
          }}
        />
      ))}
    </div>
  );
};
