import { useState, useEffect } from 'react';
import { motion, useScroll, useTransform } from 'framer-motion';
import { Magnetic } from './Magnetic';
import './Hero.css';

const missionLogs = [
    "Initializing hardware bridge...",
    "Scanning desktop for UI depth...",
    "Provider handshake established.",
    "Bypassing runtime gate via direct key...",
    "Securing UAC orchestrator handoff...",
    "Deploying vision foundation model...",
    "Isolated Agent Desktop: STANDBY.",
    "Ready for mission commands."
];

export const Hero = () => {
    const { scrollY } = useScroll();
    const y1 = useTransform(scrollY, [0, 500], [0, 200]);
    const y2 = useTransform(scrollY, [0, 500], [0, -100]);
    const [logIndex, setLogIndex] = useState(0);

    useEffect(() => {
        const timer = setInterval(() => {
            setLogIndex(prev => (prev + 1) % missionLogs.length);
        }, 2400);
        return () => clearInterval(timer);
    }, []);

    const scrollToQuickStart = () => {
        document.getElementById('quickstart')?.scrollIntoView({ behavior: 'smooth' });
    };

    return (
        <section className="hero-container mission-gradient">
            <div className="hero-content">
                <motion.div style={{ y: y1 }} className="hero-header">
                    <motion.h1
                        initial={{ opacity: 0, y: 100 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
                        className="hero-title"
                    >
                        PIXEL
                    </motion.h1>
                    <motion.h1
                        initial={{ opacity: 0, y: 100 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 1, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
                        className="hero-title outline"
                    >
                        PILOT
                    </motion.h1>
                </motion.div>

                <motion.div
                    style={{ y: y2 }}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: 0.5, duration: 1 }}
                    className="hero-description-wrapper"
                >
                    <div className="mission-log glass-panel">
                        <span className="log-prompt">&gt;</span>
                        <motion.span 
                            key={logIndex}
                            initial={{ opacity: 0, x: 5 }}
                            animate={{ opacity: 1, x: 0 }}
                            className="log-text"
                        >
                            {missionLogs[logIndex]}
                        </motion.span>
                    </div>

                    <p className="hero-desc">
                        A cinematic command room for desktop autonomy.
                        Orchestrate native Live sessions, isolated workspaces, and secure desktop coverage in one sharp interface.
                    </p>

                    <div className="hero-actions">
                        <Magnetic strength={0.3}>
                            <button className="btn-mag-primary glow-primary" onClick={scrollToQuickStart}>
                                Initiate Launch Sequence
                            </button>
                        </Magnetic>
                    </div>
                </motion.div>
            </div>

            <motion.div
                className="scroll-indicator"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 1, duration: 1 }}
            >
                <span>SCROLL</span>
                <motion.div
                    className="scroll-line"
                    animate={{ height: [0, 60, 0], y: [0, 0, 60] }}
                    transition={{ repeat: Infinity, duration: 2, ease: "easeInOut" }}
                />
            </motion.div>
        </section>
    );
};
