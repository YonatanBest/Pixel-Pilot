import './Footer.css';
import { Magnetic } from './Magnetic';
import { Logo } from './Logo';

export const Footer = () => {
    return (
        <footer className="big-footer">
            <div className="container footer-inner">
                <div className="footer-callout">
                    <h2>READY TO<br/>PILOT?</h2>
                    <Magnetic strength={0.5}>
                        <div className="scroll-top-btn" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
                            &uarr;
                        </div>
                    </Magnetic>
                </div>
                
                <div className="footer-bottom">
                    <div className="footer-brand">
                        <Logo size={32} />
                        <span>PIXELPILOT &copy; {new Date().getFullYear()}</span>
                    </div>
                    
                    <div className="footer-links">
                        <a href="#">License</a>
                        <a href="#">GitHub</a>
                        <a href="#">Twitter</a>
                    </div>
                </div>
            </div>
        </footer>
    );
};
