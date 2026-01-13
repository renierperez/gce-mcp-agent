import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:firebase_core/firebase_core.dart';
import 'chat_screen.dart';
import 'auth_service.dart';
import 'login_page.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  
  const FirebaseOptions? firebaseOptions = FirebaseOptions(
    apiKey: "AIzaSyAhsbe9fxcqEws_VSXuAVTvk3EeHnGw3AY",
    authDomain: "autonomous-agent-479317.firebaseapp.com",
    projectId: "autonomous-agent-479317",
    storageBucket: "autonomous-agent-479317.firebasestorage.app",
    messagingSenderId: "30162433848",
    appId: "1:30162433848:web:cc4faaecdb0354e974b3cd",
    measurementId: "G-JZNRSKL534"
  );

  try {
    if (firebaseOptions != null) {
      await Firebase.initializeApp(options: firebaseOptions);
    } else {
      print("⚠️ WARNING: Firebase Config is missing. App will crash on start.");
    }
  } catch (e) {
    print("Firebase Init Error: $e");
  }

  runApp(const GceManagerApp());
}

class GceManagerApp extends StatelessWidget {
  const GceManagerApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'GCE Manager Agent',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF4285F4), // Google Blue
          brightness: Brightness.dark,
          surface: const Color(0xFF1E1E1E),
        ),
        scaffoldBackgroundColor: const Color(0xFF121212),
        textTheme: GoogleFonts.outfitTextTheme(
           Theme.of(context).textTheme.apply(
             bodyColor: Colors.white,
             displayColor: Colors.white,
           ),
        ),
      ),
      home: const AuthWrapper(),
    );
  }
}

class AuthWrapper extends StatelessWidget {
  const AuthWrapper({super.key});

  @override
  Widget build(BuildContext context) {
    // Attempt to use auth, handle if firebase not init
    try {
      return StreamBuilder(
        stream: AuthService().authStateChanges,
        builder: (context, snapshot) {
           if (snapshot.connectionState == ConnectionState.waiting) {
              return const Scaffold(body: Center(child: CircularProgressIndicator()));
           }
           if (snapshot.hasData) {
              return const ChatScreen();
           }
           // Use LoginPage if not authenticated
           return LoginPage();
        },
      );
    } catch (e) {
      return Scaffold(
        body: Center(
          child: Text("Firebase Error: $e\nDid you add the config in main.dart?"),
        ),
      );
    }
  }
}
