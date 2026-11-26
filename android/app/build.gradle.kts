// The cable-box app for phones and tablets: watches the channels a Neon Cable 42 / XCable PC shares on
// the home network (the same protocol as a desktop cable box, getsome/headend.py). Two editions, like
// the desktop app: separate app ids, names, icons, looks and ports.
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val keystore: String? = System.getenv("KEYSTORE")

android {
    namespace = "app.neoncable42.box"
    compileSdk = 36

    defaultConfig {
        minSdk = 26
        targetSdk = 35
        versionCode = (System.getenv("VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("VERSION_NAME") ?: "dev"
    }

    buildFeatures {
        buildConfig = true
        resValues = true
    }

    flavorDimensions += "edition"
    productFlavors {
        create("neon") {
            dimension = "edition"
            applicationId = "app.neoncable42.box"
            buildConfigField("String", "EDITION", "\"neon\"")
            resValue("string", "app_name", "Neon Cable 42")
        }
        create("xcable") {
            dimension = "edition"
            applicationId = "app.xcable.box"
            buildConfigField("String", "EDITION", "\"xcable\"")
            resValue("string", "app_name", "XCable")
        }
    }

    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = file(keystore)
                storeType = "pkcs12"
                storePassword = System.getenv("KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KEY_ALIAS") ?: "box"
                keyPassword = System.getenv("KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            // the same key every release, so a new APK installs over the old one; without it (local
            // test builds) the debug key
            signingConfig = if (keystore != null) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }

    // one APK per processor family: almost every phone and tablet is arm64; armeabi-v7a for old ones
    splits {
        abi {
            isEnable = true
            reset()
            include("arm64-v8a", "armeabi-v7a")
            isUniversalApk = false
        }
    }

    packaging {
        jniLibs { useLegacyPackaging = true }       // compressed native libraries: a much smaller download
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

dependencies {
    // libmpv for Android (the player the desktop app uses): plays every format the desktop does
    implementation("dev.jdtech.mpv:libmpv:1.0.0")
}
