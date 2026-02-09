import './App.css';
import { Navbar } from './components/Navbar';
import { Hero } from './components/Hero';
import { Features } from './components/Features';
import { QuickStart } from './components/QuickStart';
import { Hotkeys } from './components/Hotkeys';
import { Footer } from './components/Footer';
import { AuroraBackground } from './components/AuroraBackground';

function App() {
  return (
    <>
      <AuroraBackground />
      <Navbar />
      <main>
        <Hero />
        <Features />
        <QuickStart />
        <Hotkeys />
        <Footer />
      </main>
    </>
  );
}

export default App;
